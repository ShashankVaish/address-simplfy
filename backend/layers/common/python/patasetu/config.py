"""Every tunable in one place.

Thresholds, model ids and table names are read here and nowhere else. The rule
exists because the alternative -- a confidence threshold written as `0.8` in
four files -- guarantees that Saturday's calibration run changes three of them.

Nothing in this module touches the network or constructs a client; it is safe to
import at Lambda cold start and from a unit test.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final, Literal

Provider = Literal["aws", "local"]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Thresholds:
    """Decision boundaries for S7.

    `resolved_at` is deliberately *not* a round number chosen by taste. Day 3
    fits isotonic regression on the dev split and picks the threshold that hits
    the precision target below; whatever value that produces is written into the
    environment. The default here is a placeholder for Day 1, when the
    calibration does not exist yet.
    """

    # Above this calibrated confidence, auto-resolve without asking anyone.
    resolved_at: float = 0.80
    # Of the addresses we auto-resolve, the fraction that must be correct. This
    # is the safety constraint that *chooses* `resolved_at`, not a metric we
    # report afterwards.
    precision_target: float = 0.95
    # Two candidates at least this far apart, with near-equal scores, are
    # AMBIGUOUS rather than a guess.
    ambiguous_spread_m: float = 500.0
    # Score gap below which two candidates count as "near-equal".
    ambiguous_score_delta: float = 0.05
    # Overwriting a stored customer address needs near-certainty (FR-24).
    overwrite_at: float = 0.95


@dataclass(frozen=True)
class Retrieval:
    """S2 hybrid retrieval settings."""

    # Reciprocal Rank Fusion constant. 60 is the value from the original RRF
    # paper and is not worth tuning on 300 labelled examples.
    rrf_k: int = 60
    # Geographic constraint. 3 km around a pincode centroid or GPS hint is what
    # removes the several hundred other "Shiv Mandir"s in India.
    geo_radius_m: float = 3_000.0
    # Candidates fetched per signal before fusion.
    per_signal_size: int = 20
    # Candidates handed to the model in S3. More than this wastes tokens and
    # measurably hurts selection accuracy.
    candidates_to_model: int = 8
    # Titan Text Embeddings v2 output width; must match the kNN index mapping.
    embedding_dim: int = 1024


@dataclass(frozen=True)
class Models:
    """Bedrock model ids. The cascade runs cheap-first."""

    # Stage S3 default. Handles the large majority of addresses.
    cheap: str = "amazon.nova-lite-v1:0"
    # Escalation target: low per-field confidence, a conflict with the
    # deterministic parse, multi-script input, or non-empty alternatives.
    # Sonnet 5 rather than Opus 5: the task is choosing a landmark id from a
    # short list and filling two or three fields, which does not need the top
    # tier, and it is 2.5x cheaper per call. On a $100 credit the whole event's
    # escalations cost a few dollars either way -- OpenSearch hours are the
    # budget risk, not tokens -- but there is no reason to spend the extra.
    strong: str = "anthropic.claude-sonnet-5"
    embedding: str = "amazon.titan-embed-text-v2:0"
    # Build It fallback: the Ollama tag served locally. Reached over HTTP on
    # localhost, so it needs no credentials and no network.
    local_model: str = "llama3.2"
    local_endpoint: str = "http://127.0.0.1:11434"
    # One retry on invalid JSON with a stricter reminder, then give up and fall
    # back to the deterministic result. Never loop on a model.
    json_retries: int = 1
    max_tokens: int = 1_024
    # Deterministic decoding: this is an extraction task, not a creative one.
    temperature: float = 0.0


@dataclass(frozen=True)
class Config:
    provider: Provider
    region: str
    table_name: str
    bucket_name: str
    opensearch_endpoint: str
    landmark_index: str
    # Amazon Location Service place index, for the S4 geocoder fallback.
    place_index: str
    event_bus: str
    # Cache lifetime. Long, because a resolved doorstep does not move.
    cache_ttl_days: int
    log_level: str
    thresholds: Thresholds = field(default_factory=Thresholds)
    retrieval: Retrieval = field(default_factory=Retrieval)
    models: Models = field(default_factory=Models)

    @property
    def is_local(self) -> bool:
        return self.provider == "local"


def load() -> Config:
    """Read configuration from the environment.

    Called once per Lambda container. Raises on a malformed numeric value rather
    than silently falling back to a default, because a threshold that quietly
    reverts to 0.8 after a typo is the kind of bug that only shows up in the
    evaluation numbers.
    """
    provider = _env("PROVIDER", "aws")
    if provider not in ("aws", "local"):
        raise ValueError(f"PROVIDER must be 'aws' or 'local', got {provider!r}")

    return Config(
        provider=provider,  # type: ignore[arg-type]
        region=_env("AWS_REGION", "ap-south-1"),
        table_name=_env("TABLE_NAME", "patasetu"),
        bucket_name=_env("BUCKET_NAME", ""),
        opensearch_endpoint=_env("OPENSEARCH_ENDPOINT", ""),
        landmark_index=_env("LANDMARK_INDEX", "landmarks"),
        place_index=_env("PLACE_INDEX", "patasetu-places"),
        event_bus=_env("EVENT_BUS", "patasetu"),
        cache_ttl_days=_env_int("CACHE_TTL_DAYS", 90),
        log_level=_env("LOG_LEVEL", "INFO"),
        thresholds=Thresholds(
            resolved_at=_env_float("CONF_RESOLVED_AT", 0.80),
            precision_target=_env_float("PRECISION_TARGET", 0.95),
            ambiguous_spread_m=_env_float("AMBIGUOUS_SPREAD_M", 500.0),
            ambiguous_score_delta=_env_float("AMBIGUOUS_SCORE_DELTA", 0.05),
            overwrite_at=_env_float("CONF_OVERWRITE_AT", 0.95),
        ),
        retrieval=Retrieval(
            rrf_k=_env_int("RRF_K", 60),
            geo_radius_m=_env_float("GEO_RADIUS_M", 3_000.0),
        ),
        models=Models(
            cheap=_env("MODEL_CHEAP", "amazon.nova-lite-v1:0"),
            strong=_env("MODEL_STRONG", "anthropic.claude-sonnet-5"),
            embedding=_env("MODEL_EMBEDDING", "amazon.titan-embed-text-v2:0"),
            local_model=_env("LOCAL_MODEL", "llama3.2"),
            local_endpoint=_env("LOCAL_ENDPOINT", "http://127.0.0.1:11434"),
        ),
    )


# Data file locations. Three places are tried, in order:
#
#   1. $DATA_DIR                      explicit override
#   2. <package>/data                 inside the Lambda layer (/opt/python/
#                                     patasetu/data), populated by
#                                     scripts/prepare_layer.py before `sam build`
#   3. backend/data                   the repository, for scripts and tests
#
# Order 2 before 3 matters: in a Lambda the repository path does not exist, and
# resolving it would point at /data, which fails on the first request rather
# than at import -- the worst possible time.
_HERE: Final = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT: Final = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))


def _find_data_dir() -> str:
    explicit = os.environ.get("DATA_DIR")
    if explicit:
        return explicit
    packaged = os.path.join(_HERE, "data")
    if os.path.isfile(os.path.join(packaged, "pincodes.csv")):
        return packaged
    return os.path.join(_BACKEND_ROOT, "data")


DATA_DIR: Final = _find_data_dir()
EVAL_DATA_DIR: Final = os.path.join(_BACKEND_ROOT, "eval", "data")
# Prompts ship inside the package so the layer always carries them.
PROMPT_DIR: Final = os.path.join(_HERE, "prompts")
