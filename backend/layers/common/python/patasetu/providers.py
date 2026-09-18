"""The cloud/local switch. One module, one environment variable.

    PROVIDER=aws     Bedrock, OpenSearch Serverless, DynamoDB, Location Service
    PROVIDER=local   in-process equivalents, no network, no credentials

This is the module that makes the Build It fallback a configuration change
rather than a rewrite, and it is written on Day 1-2 rather than Day 3 for
exactly that reason. Every stage that touches an external service asks this
module for a provider and never constructs a client itself.

The local implementations are not stubs that return canned answers. The local
search engine implements real BM25, real cosine kNN and a real geo filter over
an in-process index; the local embedder produces real vectors. That distinction
matters twice over:

*   The retrieval stage can be **measured**, not merely mocked, before Bedrock
    access is granted -- so the ablation has real numbers for what retrieval
    contributes.
*   Every module gets unit tests that exercise the actual code path rather than
    a mock of it, which is the only kind of test that catches a bug in the
    fusion arithmetic.

What local mode cannot fake is a language model. `LocalModel` talks to Ollama
over HTTP if it is running, and raises `ProviderUnavailable` if it is not. It
never invents a structured address, because a fabricated S3 result would make
the ablation table a work of fiction.
"""

from __future__ import annotations

import functools
from typing import Protocol, runtime_checkable

from patasetu.config import Config
from patasetu.config import load as load_config
from patasetu.models import LandmarkRecord


class ProviderUnavailable(RuntimeError):
    """A provider was asked for and cannot be served.

    Raised rather than silently degraded. A missing embedder that quietly
    returns zero vectors would leave kNN retrieval returning arbitrary
    candidates, and the ablation would report the result as though vector
    search had worked.
    """


@runtime_checkable
class Embedder(Protocol):
    """Text to a fixed-width vector."""

    dim: int

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts.

        A separate method because batching is a real cost lever: corpus
        ingestion embeds thousands of landmark names, and one call per row is
        both slower and more expensive than one call per batch.
        """
        ...


@runtime_checkable
class SearchEngine(Protocol):
    """The landmark index: BM25 + kNN + geo, in one round trip."""

    def ensure_index(self) -> None: ...

    def upsert(self, records: list[LandmarkRecord]) -> int: ...

    def count(self) -> int: ...

    def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float] | None,
        centre: tuple[float, float] | None,
        radius_m: float,
        size: int,
    ) -> dict[str, list[tuple[str, float]]]:
        """Return per-signal ranked hits, keyed by signal name.

        Deliberately returns each signal's ranking *separately* rather than one
        blended list. Reciprocal Rank Fusion consumes ranks, so fusing inside
        the engine would throw away the input the fusion needs -- and would make
        it impossible to report which signal found a landmark, which is what the
        evidence list shows the operator.
        """
        ...

    def get(self, landmark_id: str) -> LandmarkRecord | None: ...


@runtime_checkable
class StructuringModel(Protocol):
    """An LLM constrained to emit JSON matching our schema."""

    name: str

    def complete_json(self, system: str, user: str, *, max_tokens: int) -> str: ...


@runtime_checkable
class Geocoder(Protocol):
    """Address text to a coordinate. The fallback between graph and centroid."""

    def geocode(
        self, text: str, *, near: tuple[float, float] | None = None
    ) -> tuple[float, float, float] | None:
        """Return (lat, lng, accuracy_m), or None if nothing was found."""
        ...


@runtime_checkable
class KeyValueStore(Protocol):
    """The cache and audit store."""

    def get(self, pk: str, sk: str) -> dict | None: ...

    def put(
        self, pk: str, sk: str, item: dict, *, ttl_days: int | None = None
    ) -> None: ...

    def query_prefix(
        self, pk: str, sk_prefix: str = "", limit: int = 50
    ) -> list[dict]: ...


class Providers:
    """Everything the pipeline needs from the outside world.

    Constructed once per Lambda container. Each provider is built lazily, so a
    request that never reaches S3 does not pay for a Bedrock client, and a
    deployment with no OpenSearch endpoint can still serve configuration A.
    """

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or load_config()

    @property
    def is_local(self) -> bool:
        return self.cfg.is_local

    @functools.cached_property
    def embedder(self) -> Embedder:
        if self.cfg.is_local:
            from patasetu.embeddings import HashingEmbedder

            return HashingEmbedder(dim=self.cfg.retrieval.embedding_dim)

        from patasetu.embeddings import TitanEmbedder

        return TitanEmbedder(
            model_id=self.cfg.models.embedding,
            region=self.cfg.region,
            dim=self.cfg.retrieval.embedding_dim,
        )

    @functools.cached_property
    def search(self) -> SearchEngine:
        if self.cfg.is_local:
            from patasetu.search import InMemorySearchEngine

            return InMemorySearchEngine(dim=self.cfg.retrieval.embedding_dim)

        from patasetu.search import OpenSearchEngine

        if not self.cfg.opensearch_endpoint:
            raise ProviderUnavailable(
                "OPENSEARCH_ENDPOINT is not set; retrieval stages cannot run. "
                "Set PROVIDER=local to use the in-process engine instead."
            )
        return OpenSearchEngine(
            endpoint=self.cfg.opensearch_endpoint,
            index=self.cfg.landmark_index,
            region=self.cfg.region,
            dim=self.cfg.retrieval.embedding_dim,
        )

    @functools.cached_property
    def cheap_model(self) -> StructuringModel:
        return self._model(self.cfg.models.cheap)

    @functools.cached_property
    def strong_model(self) -> StructuringModel:
        return self._model(self.cfg.models.strong)

    def _model(self, model_id: str) -> StructuringModel:
        if self.cfg.is_local:
            from patasetu.structure import OllamaModel

            return OllamaModel(
                model_id=self.cfg.models.local_model,
                endpoint=self.cfg.models.local_endpoint,
            )

        from patasetu.structure import BedrockModel

        return BedrockModel(
            model_id=model_id,
            region=self.cfg.region,
            temperature=self.cfg.models.temperature,
        )

    @functools.cached_property
    def geocoder(self) -> Geocoder | None:
        """None in local mode: there is no offline street geocoder.

        Returning None rather than a fake is the honest option. S4 then falls
        straight from a landmark-graph miss to the pincode centroid, and the
        `geo.source` it reports says so -- which is exactly what the ablation
        needs to see.
        """
        if self.cfg.is_local:
            return None

        from patasetu.geocode import LocationServiceGeocoder

        return LocationServiceGeocoder(
            place_index=self.cfg.place_index, region=self.cfg.region
        )

    @functools.cached_property
    def store(self) -> KeyValueStore:
        if self.cfg.is_local:
            from patasetu.store import InMemoryStore

            return InMemoryStore()

        from patasetu.store import DynamoStore

        return DynamoStore(table_name=self.cfg.table_name, region=self.cfg.region)


@functools.lru_cache(maxsize=1)
def default() -> Providers:
    """The process-wide provider set, built once."""
    return Providers()


def reset() -> None:
    """Drop the cached provider set. For tests that change the environment."""
    default.cache_clear()
