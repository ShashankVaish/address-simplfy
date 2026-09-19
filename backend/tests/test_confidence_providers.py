"""S6 feature arithmetic, the calibrator, and the provider switch."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from patasetu import confidence as conf
from patasetu import providers as prov
from patasetu.config import Config
from patasetu.config import load as load_config
from patasetu.embeddings import HashingEmbedder
from patasetu.models import GeoSource
from patasetu.search import InMemorySearchEngine
from patasetu.store import InMemoryStore


class TestFeatures:
    def test_weights_sum_to_one(self) -> None:
        assert math.isclose(sum(conf.WEIGHTS.values()), 1.0)

    def test_completeness_weights_the_building_most(self) -> None:
        assert conf.field_completeness({"building"}) > conf.field_completeness(
            {"state", "district"}
        )
        assert conf.field_completeness(set()) == 0.0
        assert math.isclose(
            conf.field_completeness(
                {
                    "building",
                    "street",
                    "sub_locality",
                    "locality",
                    "city",
                    "district",
                    "state",
                    "pincode",
                }
            ),
            1.0,
        )

    def test_observation_weight_is_log_scaled_and_saturates(self) -> None:
        assert conf.observation_weight(0) == 0.0
        assert (
            0
            < conf.observation_weight(1)
            < conf.observation_weight(5)
            < conf.observation_weight(47)
        )
        assert conf.observation_weight(50) == 1.0 == conf.observation_weight(500)

    def test_geo_tier_ordering(self) -> None:
        assert (
            conf.GEO_TIER[GeoSource.LANDMARK_GRAPH]
            > conf.GEO_TIER[GeoSource.GEOCODER]
            > conf.GEO_TIER[GeoSource.PINCODE_CENTROID]
        )

    def test_candidate_separation(self, cfg: Config) -> None:
        assert conf.candidate_separation([0.5], [], cfg) == 1.0
        assert (
            conf.candidate_separation([0.9, 0.2], [0, 5000], cfg) == 1.0
        )  # clear winner
        assert (
            conf.candidate_separation([0.50, 0.49], [0, 100], cfg) == 0.8
        )  # tied but co-located
        assert (
            conf.candidate_separation([0.50, 0.50], [0, 5000], cfg) == 0.0
        )  # tied and far apart

    def test_score_is_bounded_and_penalties_multiply(self) -> None:
        full = conf.Features(
            field_completeness=1,
            pincode_locality_agreement=1,
            landmark_match=1,
            landmark_observations=1,
            geo_source_tier=1,
            candidate_separation=1,
            model_self_confidence=1,
        )
        assert math.isclose(conf.score(full), 1.0)
        full.penalties.append(("x", 0.5))
        assert math.isclose(conf.score(full), 0.5)
        assert conf.score(conf.Features()) >= 0.0

    def test_configuration_a_ceiling(self) -> None:
        """With no retrieval the score cannot reach 0.80. Documented in learnings."""
        a = conf.Features(
            field_completeness=1,
            pincode_locality_agreement=1,
            geo_source_tier=0.3,
            candidate_separation=1,
            model_self_confidence=0.5,
        )
        assert conf.score(a) < 0.80
        assert math.isclose(conf.score(a), 0.59, abs_tol=0.005)


class TestCalibrator:
    def test_identity_until_fitted(self) -> None:
        c = conf.Calibrator(None)
        assert not c.is_fitted and c(0.37) == 0.37
        assert "uncalibrated" in c.describe()

    def test_interpolates_between_knots(self) -> None:
        c = conf.Calibrator([(0.0, 0.0), (0.5, 0.2), (1.0, 1.0)])
        assert c.is_fitted
        assert c(0.25) == pytest.approx(0.1)
        assert c(0.75) == pytest.approx(0.6)
        assert c(-1) == 0.0 and c(2) == 1.0

    def test_loads_from_file_or_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The autouse fixture stubs `load`; this test is about the real one.
        monkeypatch.undo()
        assert not conf.Calibrator.load(str(tmp_path / "missing.json")).is_fitted
        p = tmp_path / "calibration.json"
        p.write_text(json.dumps({"knots": [[0, 0], [1, 1]]}), encoding="utf-8")
        assert conf.Calibrator.load(str(p)).is_fitted


class TestProviders:
    def test_local_mode_builds_offline_implementations(self, cfg: Config) -> None:
        p = prov.Providers(cfg)
        assert p.is_local
        assert isinstance(p.embedder, HashingEmbedder)
        assert isinstance(p.search, InMemorySearchEngine)
        assert isinstance(p.store, InMemoryStore)
        assert p.geocoder is None  # no offline street geocoder, and we say so

    def test_local_model_is_ollama_and_raises_when_absent(self, cfg: Config) -> None:
        from patasetu.structure import OllamaModel

        p = prov.Providers(cfg)
        assert isinstance(p.cheap_model, OllamaModel)
        with pytest.raises(prov.ProviderUnavailable):
            OllamaModel(endpoint="http://127.0.0.1:1").complete_json("s", "u")

    def test_aws_mode_requires_opensearch_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROVIDER", "aws")
        monkeypatch.setenv("OPENSEARCH_ENDPOINT", "")
        p = prov.Providers(load_config())
        assert not p.is_local
        with pytest.raises(prov.ProviderUnavailable):
            _ = p.search

    def test_aws_mode_builds_cloud_clients_lazily(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No credentials needed to *construct* these; only to call them.
        monkeypatch.setenv("PROVIDER", "aws")
        monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://example.aoss.amazonaws.com")
        from patasetu.embeddings import TitanEmbedder
        from patasetu.geocode import LocationServiceGeocoder
        from patasetu.search import OpenSearchEngine
        from patasetu.store import DynamoStore
        from patasetu.structure import BedrockModel

        p = prov.Providers(load_config())
        assert isinstance(p.embedder, TitanEmbedder)
        assert isinstance(p.search, OpenSearchEngine)
        assert isinstance(p.cheap_model, BedrockModel)
        assert isinstance(p.geocoder, LocationServiceGeocoder)
        assert isinstance(p.store, DynamoStore)

    def test_invalid_provider_name_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROVIDER", "azure")
        with pytest.raises(ValueError):
            load_config()

    def test_default_is_cached_and_resettable(self) -> None:
        a = prov.default()
        assert prov.default() is a
        prov.reset()
        assert prov.default() is not a
        assert os.environ.get("PROVIDER") == "local"
