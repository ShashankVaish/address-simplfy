"""Shared fixtures.

The landmark fixture is a small, realistic neighbourhood in West Delhi around
pincode 110015, built from the same record type the real index holds. It gives
every Day 2 test the same three things a real request has: a locality the
address is *in*, landmarks it is *near*, and decoys that share a word with the
right answer. Tests that need the real 263-landmark corpus index say so and skip
when the data file is absent.

`FakeModel` stands in for Bedrock in the S3 tests. It is a scripted responder,
not a language model, and it exists to test the *wiring* -- JSON extraction,
retry, escalation, conflict handling -- which is exactly what cannot be tested
against a real model deterministically. It is never used to produce a number
that gets reported.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("PROVIDER", "local")

from patasetu import gazetteer
from patasetu.config import Config
from patasetu.config import load as load_config
from patasetu.embeddings import HashingEmbedder, landmark_embedding_text
from patasetu.models import LandmarkRecord
from patasetu.providers import Providers
from patasetu.search import InMemorySearchEngine

DIM = 64  # small, so tests are fast; the engine is dimension-agnostic

# Real coordinates around Ramesh Nagar, New Delhi (pincode 110015).
NEIGHBOURHOOD: list[dict] = [
    {
        "landmark_id": "LMK#DL#RAMESH",
        "canonical_name": "Ramesh Nagar",
        "aliases": ["ramesh nagar", "रमेश नगर"],
        "type": "locality",
        "lat": 28.6519,
        "lng": 77.1200,
        "pincode": "110015",
        "observation_count": 12,
    },
    {
        "landmark_id": "LMK#DL#SHIV",
        "canonical_name": "Shiv Mandir",
        "aliases": ["shiv temple", "शिव मंदिर", "mandir wali gali"],
        "type": "religious",
        "lat": 28.6524,
        "lng": 77.1206,
        "pincode": "110015",
        "observation_count": 47,
    },
    {
        "landmark_id": "LMK#DL#GUPTA",
        "canonical_name": "Gupta General Store",
        "aliases": ["gupta store", "gupta kirana"],
        "type": "commercial",
        "lat": 28.6521,
        "lng": 77.1198,
        "pincode": "110015",
        "observation_count": 3,
    },
    {
        "landmark_id": "LMK#DL#TANK",
        "canonical_name": "Water Tank",
        "aliases": ["paani ki tanki"],
        "type": "civic",
        "lat": 28.6530,
        "lng": 77.1210,
        "pincode": "110015",
        "observation_count": 5,
    },
    # A decoy: shares the word "mandir" but is 2.5 km away.
    {
        "landmark_id": "LMK#DL#HANUMAN",
        "canonical_name": "Hanuman Mandir",
        "aliases": ["hanuman temple"],
        "type": "religious",
        "lat": 28.6700,
        "lng": 77.1400,
        "pincode": "110027",
        "observation_count": 8,
    },
    # A far decoy with the same name: the "400 other Shiv Mandirs" problem.
    {
        "landmark_id": "LMK#MH#SHIV",
        "canonical_name": "Shiv Mandir",
        "aliases": ["shiv temple"],
        "type": "religious",
        "lat": 19.0760,
        "lng": 72.8777,
        "pincode": "400001",
        "observation_count": 20,
    },
]


@pytest.fixture(scope="session", autouse=True)
def _warm_gazetteer() -> None:
    gazetteer.warm()


@pytest.fixture(scope="session")
def cfg() -> Config:
    return load_config()


@pytest.fixture(scope="session")
def embedder() -> HashingEmbedder:
    return HashingEmbedder(dim=DIM)


@pytest.fixture(scope="session")
def neighbourhood(embedder: HashingEmbedder) -> list[LandmarkRecord]:
    records = []
    for raw in NEIGHBOURHOOD:
        record = LandmarkRecord(**raw)
        record.embedding = embedder.embed(
            landmark_embedding_text(record.canonical_name, record.aliases)
        )
        records.append(record)
    return records


@pytest.fixture
def engine(neighbourhood: list[LandmarkRecord]) -> InMemorySearchEngine:
    """A fresh index per test, so upsert tests cannot leak into each other."""
    e = InMemorySearchEngine(dim=DIM)
    e.upsert(neighbourhood)
    return e


@pytest.fixture
def providers(
    cfg: Config, engine: InMemorySearchEngine, embedder: HashingEmbedder
) -> Providers:
    """Local providers with the fixture index and a matching-dimension embedder."""
    p = Providers(cfg)
    # Override the lazily-built defaults with the test fixtures.
    p.__dict__["search"] = engine
    p.__dict__["embedder"] = embedder
    return p


class FakeModel:
    """Scripted stand-in for a Bedrock model.

    `responses` are returned in order; the last one repeats. `calls` records
    every prompt, so a test can assert the retry reminder was appended or that
    escalation happened.
    """

    def __init__(self, *responses: str | Exception, name: str = "fake") -> None:
        self._responses = list(responses) or ["{}"]
        self.name = name
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls.append((system, user))
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        response = self._responses[index]
        if isinstance(response, Exception):
            raise response
        return response


def good_model_json(**overrides: object) -> str:
    """A well-formed S3 response for the Ramesh Nagar address."""
    payload = {
        "building": "14",
        "street": None,
        "sub_locality": None,
        "locality": "Ramesh Nagar",
        "city": "New Delhi",
        "district": "West",
        "state": "Delhi",
        "pincode": "110015",
        "landmarks": [
            {"name": "Shiv Mandir", "relation": "behind", "matched_id": "LMK#DL#SHIV"},
            {
                "name": "Gupta General Store",
                "relation": "near",
                "matched_id": "LMK#DL#GUPTA",
            },
        ],
        "field_confidence": {"building": 0.95, "locality": 0.98, "pincode": 1.0},
        "field_reason": {"building": "'house number 14'"},
        "conflicts": [],
        "alternatives": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.fixture
def fake_model_factory() -> type[FakeModel]:
    return FakeModel


CORPUS_INDEX = Path(__file__).resolve().parents[1] / "eval" / "data" / "landmarks.jsonl"


@pytest.fixture(scope="session")
def corpus_engine() -> Iterator[InMemorySearchEngine]:
    """The real 263-landmark index, for integration-style tests."""
    if not CORPUS_INDEX.exists():
        pytest.skip("landmarks.jsonl not built; run scripts.warm_landmarks")
    from scripts.warm_landmarks import load_landmarks

    e = InMemorySearchEngine(dim=1024)
    e.upsert(load_landmarks(CORPUS_INDEX))
    yield e
