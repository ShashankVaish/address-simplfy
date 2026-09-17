"""The landmark index: BM25 + kNN + geo, in one round trip.

Two implementations of the same interface:

*   `OpenSearchEngine` -- OpenSearch Serverless. Issues all signals in a single
    `_msearch` request, which is one HTTP round trip (NFR-04) while still
    returning each signal's ranking separately, because that is what Reciprocal
    Rank Fusion consumes.
*   `InMemorySearchEngine` -- the same three signals implemented in pure Python
    over an in-process index. Real BM25 with the standard parameters, real
    cosine kNN, real haversine geo filtering. No Docker, no credentials.

Why both signals are *filtered* by geography but geography is also its own
ranking: `geo_distance` is a filter, so it belongs on the lexical and vector
queries to remove the several hundred other "Shiv Mandir"s in India. But
proximity is *also* evidence -- of two landmarks that match the text equally
well, the closer one is more likely the one meant -- so distance contributes a
third ranked list to the fusion. Using it only as a filter throws that away;
using it only as a ranking lets a distant exact-name match win.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any

from patasetu.embeddings import cosine, is_zero
from patasetu.gazetteer import haversine_m
from patasetu.models import LandmarkRecord
from patasetu.providers import ProviderUnavailable

# Signal names. These are the keys of the dict `hybrid_search` returns and the
# labels that appear in the evidence list, so they are part of the contract.
SIGNAL_LEXICAL = "bm25"
SIGNAL_VECTOR = "knn"
SIGNAL_GEO = "geo"

# BM25 parameters. The standard values from the original paper; not worth tuning
# on a few hundred labelled examples, where any gain would be noise.
BM25_K1 = 1.2
BM25_B = 0.75


def tokenise(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, Devanagari included.

    Deliberately simple and *identical* on both engines. A tokeniser that
    differs between the index and the query is the classic cause of a search
    that works locally and returns nothing in production.
    """
    out: list[str] = []
    current: list[str] = []
    for ch in text.casefold():
        if ch.isalnum() or "ऀ" <= ch <= "ॿ":
            current.append(ch)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def landmark_text(record: LandmarkRecord) -> str:
    """The searchable text of a landmark: canonical name plus every alias."""
    return " ".join([record.canonical_name, *record.aliases])


class InMemorySearchEngine:
    """BM25 + cosine kNN + geo filter over an in-process index.

    Used for `PROVIDER=local` and by the whole test suite. Because it is a real
    implementation rather than a mock, a bug in the fusion arithmetic or the
    geo filter fails a unit test here instead of surviving until the OpenSearch
    integration is wired.
    """

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self._records: dict[str, LandmarkRecord] = {}
        self._vectors: dict[str, list[float]] = {}
        self._tokens: dict[str, list[str]] = {}
        self._doc_freq: dict[str, int] = defaultdict(int)
        self._total_len = 0

    # --- index management --------------------------------------------------
    def ensure_index(self) -> None:
        """No-op: an in-process dict needs no mapping."""

    def count(self) -> int:
        return len(self._records)

    def get(self, landmark_id: str) -> LandmarkRecord | None:
        return self._records.get(landmark_id)

    def all_records(self) -> list[LandmarkRecord]:
        return list(self._records.values())

    def upsert(self, records: list[LandmarkRecord]) -> int:
        """Insert or replace landmarks, maintaining the BM25 statistics.

        An upsert of an existing id must *remove* the old document's
        contribution to the document frequencies first. Skipping that step makes
        IDF drift downward every time the graph is re-warmed, which silently
        flattens the lexical ranking.
        """
        for record in records:
            if record.landmark_id in self._records:
                self._remove_stats(record.landmark_id)

            tokens = tokenise(landmark_text(record))
            self._records[record.landmark_id] = record
            self._tokens[record.landmark_id] = tokens
            self._total_len += len(tokens)
            for token in set(tokens):
                self._doc_freq[token] += 1

            vector = record.embedding if record.embedding else None
            if vector is not None and len(vector) != self.dim:
                raise ValueError(
                    f"{record.landmark_id}: embedding has {len(vector)} dims, "
                    f"index expects {self.dim}"
                )
            if vector is not None:
                self._vectors[record.landmark_id] = vector
        return len(records)

    def _remove_stats(self, landmark_id: str) -> None:
        tokens = self._tokens.pop(landmark_id, [])
        self._total_len -= len(tokens)
        for token in set(tokens):
            self._doc_freq[token] -= 1
            if self._doc_freq[token] <= 0:
                del self._doc_freq[token]
        self._vectors.pop(landmark_id, None)

    @property
    def avg_doc_len(self) -> float:
        return self._total_len / len(self._records) if self._records else 0.0

    # --- signals -----------------------------------------------------------
    def bm25(self, query: str, candidates: list[str]) -> list[tuple[str, float]]:
        """Okapi BM25 over the candidate ids, highest score first."""
        query_tokens = tokenise(query)
        if not query_tokens or not candidates:
            return []

        n_docs = len(self._records)
        avgdl = self.avg_doc_len or 1.0
        scored: list[tuple[str, float]] = []

        for doc_id in candidates:
            tokens = self._tokens.get(doc_id)
            if not tokens:
                continue
            freqs: dict[str, int] = defaultdict(int)
            for token in tokens:
                freqs[token] += 1

            doc_len = len(tokens)
            score = 0.0
            for token in query_tokens:
                tf = freqs.get(token, 0)
                if tf == 0:
                    continue
                df = self._doc_freq.get(token, 0)
                # Standard BM25 IDF. The +0.5 terms keep it finite and positive
                # even for a term appearing in every document.
                idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
                denom = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * doc_len / avgdl)
                score += idf * (tf * (BM25_K1 + 1.0)) / denom
            if score > 0.0:
                scored.append((doc_id, score))

        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored

    def knn(
        self, vector: list[float] | None, candidates: list[str]
    ) -> list[tuple[str, float]]:
        """Cosine similarity ranking. Empty when no query vector is available."""
        if is_zero(vector) or not candidates:
            return []
        assert vector is not None
        scored = [
            (doc_id, cosine(vector, self._vectors[doc_id]))
            for doc_id in candidates
            if doc_id in self._vectors
        ]
        # Discard non-positive similarity: with a signed hashing embedder a
        # negative cosine means the names actively disagree, and ranking those
        # would hand S3 candidates that are worse than nothing.
        scored = [pair for pair in scored if pair[1] > 0.0]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored

    def geo_candidates(
        self, centre: tuple[float, float] | None, radius_m: float
    ) -> tuple[list[str], dict[str, float]]:
        """Ids within `radius_m` of `centre`, plus their distances.

        With no centre every landmark is a candidate and no distance is known --
        which is the correct behaviour for an address with no pincode and no GPS
        hint, and is exactly the case where precision drops.
        """
        if centre is None:
            return list(self._records), {}

        lat, lng = centre
        ids: list[str] = []
        distances: dict[str, float] = {}
        for doc_id, record in self._records.items():
            d = haversine_m(lat, lng, record.lat, record.lng)
            if d <= radius_m:
                ids.append(doc_id)
                distances[doc_id] = d
        return ids, distances

    def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float] | None,
        centre: tuple[float, float] | None,
        radius_m: float,
        size: int,
    ) -> dict[str, list[tuple[str, float]]]:
        candidates, distances = self.geo_candidates(centre, radius_m)

        lexical = self.bm25(query_text, candidates)[:size]
        vector = self.knn(query_vector, candidates)[:size]

        # Proximity as its own ranking: nearest first, scored so that closer is
        # higher, for symmetry with the other two signals.
        geo = sorted(distances.items(), key=lambda pair: pair[1])[:size]
        geo_ranked = [
            (doc_id, 1.0 - min(1.0, d / radius_m)) for doc_id, d in geo
        ]

        return {
            SIGNAL_LEXICAL: lexical,
            SIGNAL_VECTOR: vector,
            SIGNAL_GEO: geo_ranked,
        }

    def distances_from(
        self, centre: tuple[float, float], ids: list[str]
    ) -> dict[str, float]:
        """Metres from `centre` to each landmark. Used by S6's spread feature."""
        out: dict[str, float] = {}
        for doc_id in ids:
            record = self._records.get(doc_id)
            if record is not None:
                out[doc_id] = haversine_m(centre[0], centre[1], record.lat, record.lng)
        return out


# The index mapping. Kept here rather than in a script so the engine and the
# thing that creates the index cannot disagree about the dimension.
def index_mapping(dim: int) -> dict[str, Any]:
    return {
        "settings": {
            "index.knn": True,
            # One shard: the corpus is thousands of landmarks, not millions, and
            # a single shard removes cross-shard scoring variance that would
            # make BM25 results depend on document placement.
            "index.number_of_shards": 1,
            "index.number_of_replicas": 0,
        },
        "mappings": {
            "properties": {
                "landmark_id": {"type": "keyword"},
                "canonical_name": {
                    "type": "text",
                    "fields": {"kw": {"type": "keyword"}},
                },
                "aliases": {"type": "text"},
                "type": {"type": "keyword"},
                "location": {"type": "geo_point"},
                "digipin_cell": {"type": "keyword"},
                "pincode": {"type": "keyword"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dim,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "faiss",
                    },
                },
                "observation_count": {"type": "integer"},
                "confidence": {"type": "float"},
                "access_notes": {"type": "object", "enabled": False},
                "last_seen": {"type": "date"},
            }
        },
    }


class OpenSearchEngine:
    """OpenSearch Serverless client for the `landmarks` index."""

    def __init__(self, endpoint: str, index: str, region: str, dim: int = 1024) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.index = index
        self.region = region
        self.dim = dim
        self._client: Any = None

    @property
    def client(self) -> Any:
        """A SigV4-signed OpenSearch client, built on first use."""
        if self._client is None:
            try:
                import boto3
                from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection
            except ImportError as exc:
                raise ProviderUnavailable(
                    "opensearch-py and boto3 are required for OpenSearchEngine; "
                    "set PROVIDER=local to use the in-process engine"
                ) from exc

            credentials = boto3.Session().get_credentials()
            host = self.endpoint.replace("https://", "")
            self._client = OpenSearch(
                hosts=[{"host": host, "port": 443}],
                http_auth=AWSV4SignerAuth(credentials, self.region, "aoss"),
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                pool_maxsize=20,
            )
        return self._client

    def ensure_index(self) -> None:
        if not self.client.indices.exists(index=self.index):
            self.client.indices.create(index=self.index, body=index_mapping(self.dim))

    def count(self) -> int:
        try:
            return int(self.client.count(index=self.index)["count"])
        except Exception:
            return 0

    def get(self, landmark_id: str) -> LandmarkRecord | None:
        try:
            doc = self.client.get(index=self.index, id=landmark_id)
        except Exception:
            return None
        return self._to_record(doc.get("_source") or {})

    @staticmethod
    def _to_record(source: dict[str, Any]) -> LandmarkRecord:
        location = source.get("location") or {}
        return LandmarkRecord(
            landmark_id=source.get("landmark_id", ""),
            canonical_name=source.get("canonical_name", ""),
            aliases=source.get("aliases") or [],
            type=source.get("type"),
            lat=float(location.get("lat", 0.0)),
            lng=float(location.get("lon", 0.0)),
            digipin_cell=source.get("digipin_cell"),
            pincode=source.get("pincode"),
            observation_count=int(source.get("observation_count", 1)),
            confidence=float(source.get("confidence", 0.5)),
            access_notes=source.get("access_notes") or [],
            last_seen=source.get("last_seen"),
            embedding=source.get("embedding"),
        )

    def upsert(self, records: list[LandmarkRecord]) -> int:
        """Bulk upsert. One request for the whole batch, not one per record."""
        if not records:
            return 0
        lines: list[str] = []
        for record in records:
            lines.append(
                json.dumps({"index": {"_index": self.index, "_id": record.landmark_id}})
            )
            lines.append(
                json.dumps(
                    {
                        "landmark_id": record.landmark_id,
                        "canonical_name": record.canonical_name,
                        "aliases": record.aliases,
                        "type": record.type,
                        "location": {"lat": record.lat, "lon": record.lng},
                        "digipin_cell": record.digipin_cell,
                        "pincode": record.pincode,
                        "embedding": record.embedding,
                        "observation_count": record.observation_count,
                        "confidence": record.confidence,
                        "access_notes": record.access_notes,
                        "last_seen": record.last_seen,
                    }
                )
            )
        body = "\n".join(lines) + "\n"
        response = self.client.bulk(body=body)
        if response.get("errors"):
            failed = [
                item["index"]["error"]
                for item in response.get("items", [])
                if item.get("index", {}).get("error")
            ]
            raise ProviderUnavailable(f"bulk upsert had {len(failed)} failures: {failed[:3]}")
        return len(records)

    def _geo_filter(
        self, centre: tuple[float, float] | None, radius_m: float
    ) -> list[dict[str, Any]]:
        if centre is None:
            return []
        return [
            {
                "geo_distance": {
                    "distance": f"{int(radius_m)}m",
                    "location": {"lat": centre[0], "lon": centre[1]},
                }
            }
        ]

    def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float] | None,
        centre: tuple[float, float] | None,
        radius_m: float,
        size: int,
    ) -> dict[str, list[tuple[str, float]]]:
        """All three signals in a single `_msearch` round trip.

        `_msearch` rather than one blended query, because RRF needs each
        signal's *rank*, and a blended query returns only a combined score. It
        is still one HTTP request, which is what NFR-04 asks for -- the
        requirement is about round trips, not about query count.
        """
        geo_filter = self._geo_filter(centre, radius_m)
        header = json.dumps({"index": self.index})
        bodies: list[tuple[str, dict[str, Any]]] = []

        # 1. Lexical. Exact-token matches boosted: a distinctive proper noun
        #    matching exactly is stronger evidence than a fuzzy partial.
        bodies.append(
            (
                SIGNAL_LEXICAL,
                {
                    "size": size,
                    "_source": ["landmark_id"],
                    "query": {
                        "bool": {
                            "should": [
                                {
                                    "multi_match": {
                                        "query": query_text,
                                        "fields": ["canonical_name^3", "aliases^2"],
                                        "type": "best_fields",
                                    }
                                },
                                {"term": {"canonical_name.kw": {"value": query_text, "boost": 5}}},
                            ],
                            "minimum_should_match": 1,
                            "filter": geo_filter,
                        }
                    },
                },
            )
        )

        # 2. Vector, with the same geographic filter applied.
        if not is_zero(query_vector):
            bodies.append(
                (
                    SIGNAL_VECTOR,
                    {
                        "size": size,
                        "_source": ["landmark_id"],
                        "query": {
                            "bool": {
                                "must": [
                                    {
                                        "knn": {
                                            "embedding": {
                                                "vector": query_vector,
                                                "k": size,
                                            }
                                        }
                                    }
                                ],
                                "filter": geo_filter,
                            }
                        },
                    },
                )
            )

        # 3. Proximity, nearest first.
        if centre is not None:
            bodies.append(
                (
                    SIGNAL_GEO,
                    {
                        "size": size,
                        "_source": ["landmark_id"],
                        "query": {"bool": {"filter": geo_filter}},
                        "sort": [
                            {
                                "_geo_distance": {
                                    "location": {"lat": centre[0], "lon": centre[1]},
                                    "order": "asc",
                                    "unit": "m",
                                }
                            }
                        ],
                    },
                )
            )

        payload = "".join(f"{header}\n{json.dumps(body)}\n" for _, body in bodies)
        try:
            response = self.client.msearch(body=payload)
        except Exception as exc:
            raise ProviderUnavailable(f"OpenSearch msearch failed: {exc}") from exc

        results: dict[str, list[tuple[str, float]]] = {
            SIGNAL_LEXICAL: [],
            SIGNAL_VECTOR: [],
            SIGNAL_GEO: [],
        }
        for (signal, _), payload_response in zip(
            bodies, response.get("responses", []), strict=False
        ):
            hits = (payload_response.get("hits") or {}).get("hits") or []
            if signal == SIGNAL_GEO:
                ranked = []
                for hit in hits:
                    distance = (hit.get("sort") or [radius_m])[0]
                    ranked.append(
                        (
                            hit["_id"],
                            1.0 - min(1.0, float(distance) / radius_m),
                        )
                    )
                results[signal] = ranked
            else:
                results[signal] = [
                    (hit["_id"], float(hit.get("_score") or 0.0)) for hit in hits
                ]
        return results
