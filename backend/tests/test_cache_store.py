"""Cache and store tests: the three levels, TTL, and the Decimal round trip."""

from __future__ import annotations

import time
from decimal import Decimal

from patasetu import store
from patasetu.cache import NEAR_DUPLICATE_COSINE, ResolutionCache
from patasetu.embeddings import HashingEmbedder
from patasetu.normalize import normalise
from patasetu.store import InMemoryStore, _to_dynamo, from_dynamo

RESOLVED = {
    "status": "RESOLVED",
    "confidence": 0.91,
    "structured": {"pincode": "110015"},
}
NEEDS_INFO = {"status": "NEEDS_INFO", "confidence": 0.4}


class TestInMemoryStore:
    def test_put_get(self) -> None:
        s = InMemoryStore()
        s.put("ADDR#x", "RESOLUTION", {"v": 1})
        assert s.get("ADDR#x", "RESOLUTION")["v"] == 1
        assert s.get("ADDR#x", "nope") is None

    def test_ttl_expires(self) -> None:
        s = InMemoryStore()
        s.put("k", "s", {"v": 1}, ttl_days=1)
        s._items[("k", "s")]["ttl"] = time.time() - 1  # already expired
        assert s.get("k", "s") is None
        assert len(s) == 0

    def test_query_prefix_is_sorted_by_sort_key(self) -> None:
        """The audit trail is a timeline only if EVENT#<ts> comes back ordered."""
        s = InMemoryStore()
        for ts in ("2026-09-18T10", "2026-09-18T08", "2026-09-18T09"):
            s.put("ORDER#1", f"EVENT#{ts}", {"ts": ts})
        s.put("ORDER#1", "META", {})
        events = s.query_prefix("ORDER#1", "EVENT#")
        assert [e["ts"] for e in events] == [
            "2026-09-18T08",
            "2026-09-18T09",
            "2026-09-18T10",
        ]

    def test_query_status_is_oldest_first(self) -> None:
        s = InMemoryStore()
        s.put(
            "ORDER#b", "META", {"status": "NEEDS_INFO", "created_at": "2026-09-18T09"}
        )
        s.put(
            "ORDER#a", "META", {"status": "NEEDS_INFO", "created_at": "2026-09-18T08"}
        )
        s.put("ORDER#c", "META", {"status": "RESOLVED", "created_at": "2026-09-18T07"})
        assert [r["PK"] for r in s.query_status("NEEDS_INFO")] == ["ORDER#a", "ORDER#b"]

    def test_key_helpers(self) -> None:
        assert store.address_pk("abc") == "ADDR#abc"
        assert store.order_pk("1") == "ORDER#1"
        assert store.digipin_pk("39J49445PJ") == "DIGIPIN#39J49445PJ"
        assert store.pincode_pk("110015") == "PIN#110015"


class TestDynamoConversion:
    def test_floats_become_decimals_and_back(self) -> None:
        item = {
            "confidence": 0.91,
            "geo": {"lat": 28.65213, "lng": 77.11987},
            "list": [0.1, {"x": 2.5}],
            "n": 3,
            "s": "x",
        }
        converted = _to_dynamo(item)
        assert isinstance(converted["confidence"], Decimal)
        assert isinstance(converted["geo"]["lat"], Decimal)
        assert isinstance(converted["list"][0], Decimal)
        assert converted["n"] == 3
        assert from_dynamo(converted) == item

    def test_decimal_goes_via_str_not_binary_expansion(self) -> None:
        # Decimal(0.1) has 55 digits and DynamoDB rejects it; Decimal("0.1") is fine.
        assert _to_dynamo(0.1) == Decimal("0.1")


class TestResolutionCache:
    def _cache(self, near: bool = True) -> ResolutionCache:
        return ResolutionCache(
            InMemoryStore(),
            ttl_days=90,
            embedder=HashingEmbedder(dim=64),
            near_duplicates=near,
        )

    def test_miss_then_exact_hit(self) -> None:
        c = self._cache()
        n = normalise("H.No 14, Ramesh Nagar, Delhi 110015")
        assert c.lookup(cache_key=n.cache_key, text=n.text, pincode="110015") is None
        c.put(cache_key=n.cache_key, text=n.text, pincode="110015", resolution=RESOLVED)
        hit = c.lookup(cache_key=n.cache_key, text=n.text, pincode="110015")
        assert hit is not None and hit.level == "exact"
        assert hit.resolution["confidence"] == 0.91

    def test_formatting_variants_share_an_exact_hit(self) -> None:
        c = self._cache()
        a = normalise("H.No 14, Ramesh Nagar, Delhi 110015")
        b = normalise("h no 14 ramesh nagar delhi 110015")
        c.put(cache_key=a.cache_key, text=a.text, pincode="110015", resolution=RESOLVED)
        assert (
            c.lookup(cache_key=b.cache_key, text=b.text, pincode="110015").level
            == "exact"
        )

    def test_chatter_variant_is_an_exact_hit(self) -> None:
        """S0 strips "call before coming" before hashing, so this needs no vector."""
        c = self._cache()
        a = normalise("Flat 4B, Sunrise Apartments, Sector 22, Noida 201301")
        b = normalise(
            "Flat 4B Sunrise Apartments Sector 22 Noida 201301, call before coming"
        )
        assert a.cache_key == b.cache_key
        c.put(cache_key=a.cache_key, text=a.text, pincode="201301", resolution=RESOLVED)
        assert (
            c.lookup(cache_key=b.cache_key, text=b.text, pincode="201301").level
            == "exact"
        )

    def test_near_duplicate_hit(self) -> None:
        """Reordered words hash differently but embed almost identically."""
        c = self._cache()
        a = normalise("Flat 4B, Sunrise Apartments, Sector 22, Noida 201301")
        b = normalise("Sunrise Apartments, Flat 4B, Noida, Sector 22, 201301")
        assert a.cache_key != b.cache_key
        c.put(cache_key=a.cache_key, text=a.text, pincode="201301", resolution=RESOLVED)
        hit = c.lookup(cache_key=b.cache_key, text=b.text, pincode="201301")
        assert hit is not None and hit.level == "near_duplicate"
        assert hit.similarity >= NEAR_DUPLICATE_COSINE

    def test_different_flat_is_not_a_near_duplicate(self) -> None:
        """The threshold is a safety parameter: 4B and 4C are different doors."""
        c = self._cache()
        a = normalise("Flat 4B, Sunrise Apartments, Sector 22, Noida 201301")
        b = normalise("Flat 4C, Sunrise Apartments, Sector 22, Noida 201301")
        c.put(cache_key=a.cache_key, text=a.text, pincode="201301", resolution=RESOLVED)
        assert c.lookup(cache_key=b.cache_key, text=b.text, pincode="201301") is None

    def test_near_duplicate_requires_same_pincode(self) -> None:
        c = self._cache()
        a = normalise("Sunrise Apartments, Sector 22, 201301")
        c.put(cache_key=a.cache_key, text=a.text, pincode="201301", resolution=RESOLVED)
        assert c.lookup(cache_key="other", text=a.text, pincode="110015") is None

    def test_near_duplicates_can_be_disabled(self) -> None:
        c = self._cache(near=False)
        a = normalise("Sunrise Apartments, Sector 22, 201301")
        c.put(cache_key=a.cache_key, text=a.text, pincode="201301", resolution=RESOLVED)
        assert c.lookup(cache_key="other", text=a.text, pincode="201301") is None

    def test_only_resolved_answers_are_cacheable(self) -> None:
        c = self._cache()
        assert c.should_cache(RESOLVED) is True
        assert c.should_cache(NEEDS_INFO) is False

    def test_stats(self) -> None:
        c = self._cache()
        n = normalise("x 110015")
        c.lookup(cache_key=n.cache_key, text=n.text, pincode="110015")
        c.put(cache_key=n.cache_key, text=n.text, pincode="110015", resolution=RESOLVED)
        c.lookup(cache_key=n.cache_key, text=n.text, pincode="110015")
        assert c.stats.lookups == 2 and c.stats.exact_hits == 1 and c.stats.misses == 1
        assert c.stats.hit_rate == 0.5

    def test_expired_entry_is_purged_from_vector_index(self) -> None:
        kv = InMemoryStore()
        c = ResolutionCache(kv, ttl_days=1, embedder=HashingEmbedder(dim=64))
        a = normalise("Sunrise Apartments, Sector 22, 201301")
        c.put(cache_key=a.cache_key, text=a.text, pincode="201301", resolution=RESOLVED)
        kv._items[(store.address_pk(a.cache_key), "RESOLUTION")]["ttl"] = (
            time.time() - 1
        )
        assert c.lookup(cache_key="other", text=a.text, pincode="201301") is None
        assert c._vectors["201301"] == []
