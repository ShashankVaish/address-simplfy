"""Text embeddings for the kNN half of S2.

Two implementations behind one interface:

*   `TitanEmbedder` calls Amazon Titan Text Embeddings v2 through Bedrock. 1024
    dimensions, matching the `knn_vector` mapping in the landmarks index.
*   `HashingEmbedder` is a local, dependency-free character n-gram model.

The local one deserves an explanation, because it is easy to mistake for a stub.
It is a real vector model: it hashes character trigrams into a fixed number of
buckets, weights them, and L2-normalises, so cosine similarity between two
vectors is a genuine measure of shared character structure. That gives it the
property S2 actually needs from the vector signal -- "Sunrise Apts" lands near
"Sunrise Apartment", and "Shiv Mandir" near "Shiv Mandhir" -- without a model
download, a GPU, or a network call.

What it is *not* is semantic. It has no notion that "mandir" and "temple" mean
the same thing, which Titan does. So local-mode retrieval numbers are a **lower
bound** on cloud-mode numbers, and the ablation labels them as such rather than
presenting them as the cloud result.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import re
from typing import Any

from patasetu.providers import ProviderUnavailable

# Trigrams are the sweet spot for short names: bigrams collide too readily
# across unrelated Indian place names, and 4-grams stop matching once a single
# character is misspelled -- which is exactly the case the vector signal exists
# to catch.
_NGRAM = 3

_TOKEN_RE = re.compile(r"[a-z0-9ऀ-ॿ]+")


@functools.lru_cache(maxsize=1 << 17)
def _bucket(ngram: str, dim: int) -> tuple[int, float]:
    """Map an n-gram to a bucket index and a sign.

    blake2b rather than the built-in `hash()`, which Python randomises per
    process via PYTHONHASHSEED: vectors written into the index on one run would
    not match query vectors computed on the next, and kNN would silently return
    noise. A *signed* hash also lets two distinct n-grams that collide into the
    same bucket partially cancel rather than always reinforcing each other.

    Module-level rather than a method so the cache does not retain the embedder
    instance; `dim` is part of the key because it changes the bucket.
    """
    digest = hashlib.blake2b(ngram.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if (value >> 63) & 1 else -1.0


def _normalise(text: str) -> str:
    return " ".join(_TOKEN_RE.findall(text.casefold()))


def l2_normalise(vector: list[float]) -> list[float]:
    """Scale to unit length so cosine similarity reduces to a dot product.

    A zero vector is returned unchanged rather than divided by zero. Callers
    must treat an all-zero embedding as "no vector signal available" -- see
    `is_zero`.
    """
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


def is_zero(vector: list[float] | None) -> bool:
    """True when a vector carries no signal."""
    return vector is None or not any(vector)


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, clamped to [-1, 1].

    Does not assume its inputs are normalised: the index stores unit vectors,
    but a caller passing a raw one should still get a correct answer rather than
    a silently inflated score.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / math.sqrt(na * nb)))


class HashingEmbedder:
    """Character-trigram hashing embedder. Deterministic, offline, no deps."""

    def __init__(self, dim: int = 1024) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def _ngrams(self, text: str) -> list[str]:
        """Trigrams over each token, with word-boundary padding.

        The padding matters: without it "nagar" and "agar" share every trigram
        and score as near-identical. Bounding each token with a marker makes a
        prefix difference visible to the model.
        """
        out: list[str] = []
        for token in _normalise(text).split():
            padded = f"\x02{token}\x03"
            if len(padded) <= _NGRAM:
                out.append(padded)
                continue
            out.extend(padded[i : i + _NGRAM] for i in range(len(padded) - _NGRAM + 1))
        return out

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        ngrams = self._ngrams(text)
        if not ngrams:
            return vector  # empty input -> no signal, and is_zero() says so

        # Sub-linear term frequency, as in BM25 and TF-IDF: a name that repeats
        # a trigram three times is not three times as much about it.
        counts: dict[str, int] = {}
        for gram in ngrams:
            counts[gram] = counts.get(gram, 0) + 1

        for gram, count in counts.items():
            index, sign = _bucket(gram, self.dim)
            vector[index] += sign * (1.0 + math.log(count))

        return l2_normalise(vector)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class TitanEmbedder:
    """Amazon Titan Text Embeddings v2, via Bedrock."""

    # Titan v2 accepts a maximum input length; longer text is truncated rather
    # than rejected, and a silently truncated landmark name would embed to the
    # wrong place. We truncate explicitly so it is visible in the code.
    MAX_CHARS = 8_000
    # Bedrock has no batch embedding API, so "batch" means a bounded loop. The
    # limit exists to keep one ingestion call from running for minutes.
    BATCH_LIMIT = 256

    def __init__(self, model_id: str, region: str, dim: int = 1024) -> None:
        self.model_id = model_id
        self.region = region
        self.dim = dim
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - boto3 ships in Lambda
                raise ProviderUnavailable(
                    "boto3 is required for TitanEmbedder"
                ) from exc
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def embed(self, text: str) -> list[float]:
        payload = {
            "inputText": text[: self.MAX_CHARS],
            "dimensions": self.dim,
            "normalize": True,
        }
        try:
            response = self.client.invoke_model(
                modelId=self.model_id, body=json.dumps(payload)
            )
            body = json.loads(response["body"].read())
        except Exception as exc:
            # Surfaced as ProviderUnavailable so S2 can degrade to lexical-only
            # retrieval and *say so* in the evidence, rather than returning a
            # zero vector that quietly matches everything.
            raise ProviderUnavailable(f"Titan embedding failed: {exc}") from exc

        vector = body.get("embedding")
        if not isinstance(vector, list) or len(vector) != self.dim:
            raise ProviderUnavailable(
                f"Titan returned {len(vector) if isinstance(vector, list) else '?'} "
                f"dimensions, expected {self.dim}; this would corrupt the kNN index"
            )
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if len(texts) > self.BATCH_LIMIT:
            raise ValueError(
                f"batch of {len(texts)} exceeds limit {self.BATCH_LIMIT}; "
                f"chunk it in the caller so progress is reportable"
            )
        return [self.embed(t) for t in texts]


def landmark_embedding_text(
    canonical_name: str, aliases: list[str] | None = None
) -> str:
    """The text we actually embed for a landmark.

    Name plus aliases in one string, de-duplicated. Embedding the aliases
    together rather than separately is deliberate: one vector per landmark keeps
    the index small and means a query matching any alias pulls up the landmark,
    without needing to reconcile several vectors for the same entity.
    """
    parts = [canonical_name, *(aliases or [])]
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        key = _normalise(part)
        if key and key not in seen:
            seen.add(key)
            unique.append(part.strip())
    return " ".join(unique)
