"""End-to-end pipeline tests across every stack.

These are the tests that would catch a wiring mistake between stages: a
landmark matched in S2 that never reaches S4, a model conflict that overwrites a
validated pincode, a cache that serves a NEEDS_INFO answer. Each runs the real
S0-S7 code path against the fixture neighbourhood; the model, where needed, is
the scripted FakeModel.
"""

from __future__ import annotations

import pytest

from patasetu.cache import ResolutionCache
from patasetu.config import Config
from patasetu.models import GeoSource, Status
from patasetu.pipeline import Stack, StageUnavailable, resolve
from patasetu.providers import Providers, ProviderUnavailable
from patasetu.store import InMemoryStore
from tests.conftest import FakeModel, good_model_json

ADDRESS = "h no 14 behind shiv mandir near gupta general store ramesh nagar delhi 110015 call me 9876543210"
HINDI = "शिव मंदिर के पीछे, रमेश नगर, दिल्ली 110015"


def with_model(
    providers: Providers, *responses: str | Exception, strong: FakeModel | None = None
) -> Providers:
    providers.__dict__["cheap_model"] = FakeModel(*responses, name="cheap")
    providers.__dict__["strong_model"] = strong or FakeModel(
        good_model_json(), name="strong"
    )
    return providers


class TestStackA:
    def test_deterministic_only(self, cfg: Config, providers: Providers) -> None:
        r = resolve(ADDRESS, stack=Stack.A_DETERMINISTIC, cfg=cfg, providers=providers)
        assert r.structured.pincode == "110015"
        assert r.structured.locality == "Ramesh Nagar"
        assert r.structured.building == "14"
        assert r.geo.source is GeoSource.PINCODE_CENTROID
        assert r.digipin is not None and len(r.digipin) == 10
        assert all(lm.matched_id is None for lm in r.structured.landmarks)
        assert "9876543210" not in r.model_dump_json()

    def test_cannot_auto_resolve_by_construction(
        self, cfg: Config, providers: Providers
    ) -> None:
        r = resolve(ADDRESS, stack=Stack.A_DETERMINISTIC, cfg=cfg, providers=providers)
        assert r.status is Status.NEEDS_INFO
        assert r.confidence < cfg.thresholds.resolved_at


class TestRetrievalStacks:
    @pytest.mark.parametrize("stack", [Stack.R1_BM25_NO_LLM, Stack.R2_HYBRID_NO_LLM])
    def test_landmarks_are_matched_and_geocoded_from_the_graph(
        self, cfg: Config, providers: Providers, stack: Stack
    ) -> None:
        r = resolve(ADDRESS, stack=stack, cfg=cfg, providers=providers)
        by_name = {lm.name: lm for lm in r.structured.landmarks}
        assert by_name["Shiv Mandir"].matched_id == "LMK#DL#SHIV"
        assert by_name["Shiv Mandir"].relation.value == "behind"
        assert by_name["Gupta General Store"].matched_id == "LMK#DL#GUPTA"
        assert r.geo.source is GeoSource.LANDMARK_GRAPH
        assert r.geo.is_doorstep_accurate

    def test_locality_anchor_wins_over_nearby_landmark(
        self, cfg: Config, providers: Providers
    ) -> None:
        """'X, near Y' is AT X. Geocode from X, not from Y."""
        r = resolve(
            "near gupta general store, ramesh nagar, delhi 110015",
            stack=Stack.R2_HYBRID_NO_LLM,
            cfg=cfg,
            providers=providers,
        )
        assert (r.geo.lat, r.geo.lng) == (
            28.6519,
            77.1200,
        )  # Ramesh Nagar, not the store
        assert any("anchor" in e for e in r.evidence)

    def test_far_namesake_is_excluded_by_geography(
        self, cfg: Config, providers: Providers
    ) -> None:
        r = resolve(
            "behind shiv mandir, ramesh nagar, delhi 110015",
            stack=Stack.R2_HYBRID_NO_LLM,
            cfg=cfg,
            providers=providers,
        )
        assert r.structured.landmarks[0].matched_id == "LMK#DL#SHIV"
        assert r.geo.lat > 28  # Delhi, not Mumbai

    def test_hindi_and_english_agree(self, cfg: Config, providers: Providers) -> None:
        a = resolve(HINDI, stack=Stack.R2_HYBRID_NO_LLM, cfg=cfg, providers=providers)
        b = resolve(
            "behind shiv mandir, ramesh nagar, delhi 110015",
            stack=Stack.R2_HYBRID_NO_LLM,
            cfg=cfg,
            providers=providers,
        )
        assert (
            a.structured.landmarks[0].matched_id == b.structured.landmarks[0].matched_id
        )
        assert a.geo == b.geo
        assert a.clarification is None or a.clarification.language == "hi-IN"

    def test_unknown_landmark_stays_unmatched(
        self, cfg: Config, providers: Providers
    ) -> None:
        r = resolve(
            "near sharma sweets, ramesh nagar, delhi 110015",
            stack=Stack.R2_HYBRID_NO_LLM,
            cfg=cfg,
            providers=providers,
        )
        lm = r.structured.landmarks[0]
        assert lm.name == "Sharma Sweets" and lm.matched_id is None

    def test_gps_hint_constrains_but_is_not_the_answer(
        self, cfg: Config, providers: Providers
    ) -> None:
        hint = {"lat": 28.6524, "lng": 77.1206}
        r = resolve(
            "behind shiv mandir",
            stack=Stack.R2_HYBRID_NO_LLM,
            cfg=cfg,
            providers=providers,
            hint=hint,
        )
        assert r.structured.landmarks[0].matched_id == "LMK#DL#SHIV"
        assert any("GPS hint" in e for e in r.evidence)

    def test_no_pincode_no_hint_still_retrieves(
        self, cfg: Config, providers: Providers
    ) -> None:
        r = resolve(
            "behind shiv mandir",
            stack=Stack.R2_HYBRID_NO_LLM,
            cfg=cfg,
            providers=providers,
        )
        assert r.structured.landmarks[0].matched_id is not None
        assert any("penalty" in e for e in r.evidence)

    def test_ambiguity_surfaces_both_readings(
        self, cfg: Config, providers: Providers
    ) -> None:
        # Two Shiv Mandirs, no pincode, no hint: the text cannot tell them apart.
        r = resolve(
            "behind shiv mandir",
            stack=Stack.R1_BM25_NO_LLM,
            cfg=cfg,
            providers=providers,
        )
        assert r.status is Status.AMBIGUOUS
        assert len(r.alternatives) == 2


class TestModelStacks:
    def test_b_uses_model_without_retrieval(
        self, cfg: Config, providers: Providers
    ) -> None:
        p = with_model(providers, good_model_json())
        r = resolve(ADDRESS, stack=Stack.B_LLM_ONLY, cfg=cfg, providers=p)
        assert p.cheap_model.calls and "CANDIDATES\n[]" in p.cheap_model.calls[0][1]
        assert r.geo.source is GeoSource.PINCODE_CENTROID  # graph not reachable in B
        # The model's ids must be rejected: no candidates were offered.
        assert all(lm.matched_id is None for lm in r.structured.landmarks)

    @pytest.mark.parametrize(
        "stack", [Stack.C_BM25, Stack.D_HYBRID, Stack.E_WARM_GRAPH]
    )
    def test_full_stacks_geocode_from_model_chosen_landmark(
        self, cfg: Config, providers: Providers, stack: Stack
    ) -> None:
        p = with_model(providers, good_model_json())
        r = resolve(ADDRESS, stack=stack, cfg=cfg, providers=p)
        assert "LMK#DL#SHIV" in p.cheap_model.calls[0][1]
        assert r.structured.landmarks[0].matched_id == "LMK#DL#SHIV"
        assert r.geo.source is GeoSource.LANDMARK_GRAPH
        assert r.status is Status.RESOLVED
        assert r.confidence >= cfg.thresholds.resolved_at

    def test_model_cannot_overwrite_validated_pincode(
        self, cfg: Config, providers: Providers
    ) -> None:
        p = with_model(providers, good_model_json(pincode="560029", state="Karnataka"))
        r = resolve(ADDRESS, stack=Stack.D_HYBRID, cfg=cfg, providers=p)
        assert r.structured.pincode == "110015" and r.structured.state == "Delhi"
        assert any("conflict" in e.lower() for e in r.evidence)

    def test_model_garbage_degrades_not_500(
        self, cfg: Config, providers: Providers
    ) -> None:
        p = with_model(providers, "nope", "still nope")
        r = resolve(ADDRESS, stack=Stack.D_HYBRID, cfg=cfg, providers=p)
        assert r.structured.pincode == "110015"
        assert r.status is Status.NEEDS_INFO
        assert any("s3_unavailable" in e for e in r.evidence)

    def test_unreachable_model_is_stage_unavailable(
        self, cfg: Config, providers: Providers
    ) -> None:
        p = with_model(providers, ProviderUnavailable("bedrock down"))
        with pytest.raises(StageUnavailable):
            resolve(ADDRESS, stack=Stack.D_HYBRID, cfg=cfg, providers=p)

    def test_escalation_reaches_strong_model(
        self, cfg: Config, providers: Providers
    ) -> None:
        strong = FakeModel(good_model_json(), name="strong")
        p = with_model(
            providers,
            good_model_json(field_confidence={"building": 0.1}),
            strong=strong,
        )
        resolve(ADDRESS, stack=Stack.D_HYBRID, cfg=cfg, providers=p)
        assert len(strong.calls) == 1

    def test_prompt_cap_applies_only_to_the_model(
        self, cfg: Config, providers: Providers
    ) -> None:
        p = with_model(providers, good_model_json())
        resolve(ADDRESS, stack=Stack.D_HYBRID, cfg=cfg, providers=p)
        prompt = p.cheap_model.calls[0][1]
        assert prompt.count('"id":') <= cfg.retrieval.candidates_to_model


class TestCache:
    def test_second_request_is_served_from_cache(
        self, cfg: Config, providers: Providers
    ) -> None:
        p = with_model(providers, good_model_json())
        cache = ResolutionCache(InMemoryStore(), embedder=p.embedder)
        first = resolve(
            ADDRESS, stack=Stack.D_HYBRID, cfg=cfg, providers=p, cache=cache
        )
        assert first.status is Status.RESOLVED and not first.cached
        second = resolve(
            ADDRESS.upper(), stack=Stack.D_HYBRID, cfg=cfg, providers=p, cache=cache
        )
        assert second.cached
        assert second.structured == first.structured
        assert len(p.cheap_model.calls) == 1  # the model was not called again
        assert any("cache" in e for e in second.evidence)

    def test_needs_info_is_never_cached(
        self, cfg: Config, providers: Providers
    ) -> None:
        cache = ResolutionCache(InMemoryStore(), embedder=providers.embedder)
        resolve(
            ADDRESS,
            stack=Stack.A_DETERMINISTIC,
            cfg=cfg,
            providers=providers,
            cache=cache,
        )
        assert cache.stats.misses == 1
        r = resolve(
            ADDRESS,
            stack=Stack.A_DETERMINISTIC,
            cfg=cfg,
            providers=providers,
            cache=cache,
        )
        assert not r.cached and cache.stats.misses == 2


class TestInvariants:
    @pytest.mark.parametrize(
        "stack", [Stack.A_DETERMINISTIC, Stack.R1_BM25_NO_LLM, Stack.R2_HYBRID_NO_LLM]
    )
    def test_response_shape_is_consistent(
        self, cfg: Config, providers: Providers, stack: Stack
    ) -> None:
        r = resolve(ADDRESS, stack=stack, cfg=cfg, providers=providers)
        assert 0.0 <= r.confidence <= 1.0
        assert r.evidence
        assert set(r.timings_ms) >= {
            "s0_normalise",
            "s1_parse",
            "s4_geocode",
            "s5_digipin",
            "s6_confidence",
            "s7_decide",
        }
        if r.status is Status.NEEDS_INFO:
            assert r.clarification is not None
        if r.digipin:
            assert r.geo is not None

    def test_phone_never_appears_anywhere_in_output(
        self, cfg: Config, providers: Providers
    ) -> None:
        p = with_model(providers, good_model_json())
        r = resolve(ADDRESS, stack=Stack.D_HYBRID, cfg=cfg, providers=p)
        assert "9876543210" not in r.model_dump_json()
        assert "9876543210" not in p.cheap_model.calls[0][1]  # nor in the prompt

    def test_stack_capabilities(self) -> None:
        assert (
            not Stack.A_DETERMINISTIC.uses_model
            and not Stack.A_DETERMINISTIC.uses_retrieval
        )
        assert Stack.B_LLM_ONLY.uses_model and not Stack.B_LLM_ONLY.uses_retrieval
        assert Stack.C_BM25.uses_retrieval and not Stack.C_BM25.uses_vector
        assert Stack.D_HYBRID.uses_vector and Stack.E_WARM_GRAPH.uses_vector
        assert (
            Stack.R1_BM25_NO_LLM.is_diagnostic and not Stack.R1_BM25_NO_LLM.uses_model
        )
