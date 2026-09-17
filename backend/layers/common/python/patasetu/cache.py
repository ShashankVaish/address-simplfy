"""Caching, at three levels.

    exact        sha256(normalised text)          literal repeats, free, instant
    near-dup     embedding cosine > 0.97, same    "Flat 4B Sunrise Apts" vs
                 pincode                          "4-B, Sunrise Apartment"
    landmark     landmark_id -> coordinate        reused by every future address
                                                  mentioning that landmark

The cache is not a micro-optimisation, and this is the one place where saying so
is justified by arithmetic. In any real city corpus a large share of addresses
are near-duplicates: the same apartment complexes, the same hostels, the same
office parks, written differently. An exact hit ends the request in about 12 ms
having spent one DynamoDB read and zero model tokens, against roughly 700 ms and
a Bedrock call for a miss. At a 40% hit rate that is a 40% cut in both latency
and model spend for a single point read.

The third level is the one that compounds. An address is cached as a string; a
*landmark* is cached as an entity, so the coordinate learned once is reused by
every differently-worded address that mentions it, forever. That is the
mechanism behind the learning curve, not a side effect of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from patasetu import store
from patasetu.embeddings import cosine, is_zero

# Cosine above which two normalised addresses are treated as the same doorstep.
# Deliberately high. At 0.97 with a trigram embedder the two strings differ by
# punctuation, spacing or an abbreviation -- not by a flat number. Lowering it to
# 0.90 would start merging "Flat 4B" with "Flat 4C", which is a wrong delivery,
# so the threshold is a safety parameter rather than a tuning knob.
NEAR_DUPLICATE_COSINE = 0.97

# A near-duplicate must also agree on pincode. Two identical building names in
# different cities are common in India -- "Sunrise Apartments" exists in every
# metro -- and the pincode is what keeps the vector cache from crossing them.
REQUIRE_PINCODE_MATCH = True


@dataclass
class CacheHit:
    resolution: dict[str, Any]
    level: str
    similarity: float | None = None
    evidence: list[str] = field(default_factory=list)


@dataclass
class CacheStats:
    """Counters for the CloudWatch CacheHitRate metric."""

    lookups: int = 0
    exact_hits: int = 0
    near_hits: int = 0
    misses: int = 0

    @property
    def hit_rate(self) -> float:
        return (self.exact_hits + self.near_hits) / self.lookups if self.lookups else 0.0


class ResolutionCache:
    """Levels 1 and 2. Level 3 lives in the landmark index itself."""

    def __init__(
        self,
        kv: Any,
        *,
        ttl_days: int = 90,
        embedder: Any | None = None,
        near_duplicates: bool = True,
    ) -> None:
        self.kv = kv
        self.ttl_days = ttl_days
        self.embedder = embedder
        self.near_duplicates = near_duplicates
        self.stats = CacheStats()
        # Level-2 index: pincode -> [(cache_key, vector)]. Held in the container
        # rather than queried, because a kNN round trip to find a cache entry
        # would cost more than the pipeline run it saves.
        self._vectors: dict[str, list[tuple[str, list[float]]]] = {}

    # --- level 1: exact ----------------------------------------------------
    def get_exact(self, cache_key: str) -> dict[str, Any] | None:
        item = self.kv.get(store.address_pk(cache_key), "RESOLUTION")
        if item is None:
            return None
        payload = item.get("resolution")
        return store.from_dynamo(payload) if payload else None

    # --- level 2: near-duplicate ------------------------------------------
    def get_near(
        self, text: str, pincode: str | None
    ) -> tuple[dict[str, Any], float] | None:
        """Find a cached resolution for a differently-worded same address."""
        if not self.near_duplicates or self.embedder is None:
            return None
        bucket = self._vectors.get(pincode or "")
        if not bucket:
            return None

        try:
            vector = self.embedder.embed(text)
        except Exception:
            return None
        if is_zero(vector):
            return None

        best_key: str | None = None
        best_similarity = 0.0
        for key, cached_vector in bucket:
            similarity = cosine(vector, cached_vector)
            if similarity > best_similarity:
                best_key, best_similarity = key, similarity

        if best_key is None or best_similarity < NEAR_DUPLICATE_COSINE:
            return None
        payload = self.get_exact(best_key)
        if payload is None:
            # The entry expired out from under the vector index; drop it so we
            # do not keep paying for a lookup that can never hit.
            self._vectors[pincode or ""] = [
                pair for pair in bucket if pair[0] != best_key
            ]
            return None
        return payload, best_similarity

    def lookup(
        self, *, cache_key: str, text: str, pincode: str | None
    ) -> CacheHit | None:
        """Try both levels, cheapest first."""
        self.stats.lookups += 1

        exact = self.get_exact(cache_key)
        if exact is not None:
            self.stats.exact_hits += 1
            return CacheHit(
                resolution=exact,
                level="exact",
                similarity=1.0,
                evidence=[
                    "answered from the exact cache: this normalised address has "
                    "been resolved before, at no model cost"
                ],
            )

        near = self.get_near(text, pincode)
        if near is not None:
            payload, similarity = near
            self.stats.near_hits += 1
            return CacheHit(
                resolution=payload,
                level="near_duplicate",
                similarity=similarity,
                evidence=[
                    f"answered from the near-duplicate cache: cosine "
                    f"{similarity:.3f} against a previously resolved address "
                    f"in the same pincode"
                ],
            )

        self.stats.misses += 1
        return None

    # --- writes ------------------------------------------------------------
    def put(
        self,
        *,
        cache_key: str,
        text: str,
        pincode: str | None,
        resolution: dict[str, Any],
    ) -> None:
        """Cache a resolution, and index it for near-duplicate lookup.

        Only worth caching a result we would be willing to serve again, so the
        caller is expected to skip `NEEDS_INFO`. Caching an unresolved address
        would freeze a question in place and re-ask it forever.
        """
        self.kv.put(
            store.address_pk(cache_key),
            "RESOLUTION",
            {
                "resolution": resolution,
                "cached_at": datetime.now(UTC).isoformat(),
                "pincode": pincode,
            },
            ttl_days=self.ttl_days,
        )

        if not self.near_duplicates or self.embedder is None:
            return
        try:
            vector = self.embedder.embed(text)
        except Exception:
            return
        if is_zero(vector):
            return
        bucket = self._vectors.setdefault(pincode or "", [])
        # Replace any existing entry for this key so a re-resolution does not
        # leave a stale vector pointing at the same row.
        bucket[:] = [pair for pair in bucket if pair[0] != cache_key]
        bucket.append((cache_key, vector))

    def should_cache(self, resolution: dict[str, Any]) -> bool:
        """Cache only an answer we would stand behind on a later request."""
        return resolution.get("status") == "RESOLVED"
