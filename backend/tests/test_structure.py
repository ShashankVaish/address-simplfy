"""S3 tests: JSON extraction, output validation, retry, escalation, failure modes.

All against `FakeModel`, a scripted responder. These test the wiring around the
model -- the part that has to be right regardless of which model is behind it.
"""

from __future__ import annotations

import json

import pytest

from patasetu.config import Config
from patasetu.providers import ProviderUnavailable
from patasetu.retrieve import Candidate
from patasetu.search import InMemorySearchEngine
from patasetu.structure import (
    STRICTER_REMINDER,
    EscalationReason,
    build_user_prompt,
    extract_json,
    load_prompt,
    normalise_model_output,
    should_escalate,
    structure,
)
from tests.conftest import FakeModel, good_model_json

DETERMINISTIC = {
    "building": "14",
    "locality": "Ramesh Nagar",
    "city": "New Delhi",
    "district": "West",
    "state": "Delhi",
    "pincode": "110015",
    "street": None,
    "sub_locality": None,
}


def cands(engine: InMemorySearchEngine, *ids: str) -> list[Candidate]:
    return [
        Candidate(
            landmark_id=i,
            record=engine.get(i),
            rrf_score=0.02,
            signals={"bm25": 1},
            distance_m=60.0,
        )
        for i in ids
    ]


class TestPrompt:
    def test_prompt_file_loads_and_states_the_five_rules(self) -> None:
        text = load_prompt()
        assert "null" in text and "NEVER guess" in text
        assert "matched_id" in text and "alternatives" in text

    def test_user_prompt_carries_candidates_and_settled_fields(
        self, engine: InMemorySearchEngine
    ) -> None:
        p = build_user_prompt(
            raw_text="x",
            deterministic=DETERMINISTIC,
            candidates=cands(engine, "LMK#DL#SHIV"),
        )
        assert "LMK#DL#SHIV" in p and "Shiv Mandir" in p
        assert '"pincode": "110015"' in p
        assert "street" not in p  # None fields are not sent as settled

    def test_user_prompt_never_includes_embeddings(
        self, engine: InMemorySearchEngine
    ) -> None:
        p = build_user_prompt(
            raw_text="x", deterministic={}, candidates=cands(engine, "LMK#DL#SHIV")
        )
        assert "embedding" not in p


class TestExtractJson:
    def test_plain(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_code_fenced(self) -> None:
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_prefix(self) -> None:
        assert extract_json('Here is the JSON:\n{"a": {"b": 2}} thanks') == {
            "a": {"b": 2}
        }

    def test_braces_inside_strings_do_not_confuse_it(self) -> None:
        assert extract_json('{"name": "Flat {4B}", "x": 1}') == {
            "name": "Flat {4B}",
            "x": 1,
        }

    @pytest.mark.parametrize("bad", ["", "   ", "no json here", '{"a": 1', '{"a": }'])
    def test_rejects_garbage(self, bad: str) -> None:
        with pytest.raises(ValueError):
            extract_json(bad)


class TestNormaliseOutput:
    def test_keeps_valid_fields_and_landmarks(self) -> None:
        r = normalise_model_output(
            json.loads(good_model_json()),
            deterministic={},
            candidate_ids={"LMK#DL#SHIV", "LMK#DL#GUPTA"},
        )
        assert r.fields["building"] == "14"
        assert [lm["matched_id"] for lm in r.landmarks] == [
            "LMK#DL#SHIV",
            "LMK#DL#GUPTA",
        ]
        assert r.landmarks[0]["relation"] == "behind"
        assert r.conflicts == []

    def test_refuses_to_overwrite_a_settled_field(self) -> None:
        """NFR-14: the validated parse wins; the disagreement is recorded."""
        r = normalise_model_output(
            json.loads(good_model_json(pincode="560029")),
            deterministic={"pincode": "110015"},
            candidate_ids=set(),
        )
        assert r.fields["pincode"] == "110015"
        assert any("pincode" in c and "560029" in c for c in r.conflicts)

    def test_rejects_invented_landmark_ids(self) -> None:
        """A fabricated id would send S4 to the wrong place entirely."""
        r = normalise_model_output(
            json.loads(good_model_json()),
            deterministic={},
            candidate_ids={"LMK#DL#SHIV"},
        )
        gupta = next(lm for lm in r.landmarks if "Gupta" in lm["name"])
        assert gupta["matched_id"] is None
        assert any("not in the candidate list" in c for c in r.conflicts)

    def test_drops_unknown_fields(self) -> None:
        r = normalise_model_output(
            {"building": "1", "landmark_2": "x", "evil": True},
            deterministic={},
            candidate_ids=set(),
        )
        assert set(r.fields) == {"building"}

    def test_coerces_bad_relation_to_near(self) -> None:
        r = normalise_model_output(
            {"landmarks": [{"name": "X", "relation": "somewhere"}]},
            deterministic={},
            candidate_ids=set(),
        )
        assert r.landmarks[0]["relation"] == "near"

    def test_blank_strings_become_absent(self) -> None:
        r = normalise_model_output(
            {"building": "  ", "street": None}, deterministic={}, candidate_ids=set()
        )
        assert "building" not in r.fields

    def test_confidence_is_clamped_and_filtered(self) -> None:
        r = normalise_model_output(
            {"field_confidence": {"building": 1.7, "nope": 0.5, "city": "x"}},
            deterministic={},
            candidate_ids=set(),
        )
        assert r.field_confidence == {"building": 1.0}

    def test_self_confidence_defaults_to_neutral(self) -> None:
        assert (
            normalise_model_output(
                {}, deterministic={}, candidate_ids=set()
            ).self_confidence
            == 0.5
        )


class TestEscalation:
    def test_no_reason_when_clean(self) -> None:
        r = normalise_model_output(
            json.loads(good_model_json()),
            deterministic={},
            candidate_ids={"LMK#DL#SHIV", "LMK#DL#GUPTA"},
        )
        assert should_escalate(r, multi_script=False) == []

    def test_reasons(self) -> None:
        r = normalise_model_output(
            json.loads(
                good_model_json(
                    field_confidence={"building": 0.2},
                    alternatives=[{"reason": "r", "fields": {"building": "41"}}],
                )
            ),
            deterministic={"pincode": "999999"},
            candidate_ids=set(),
        )
        reasons = should_escalate(r, multi_script=True)
        assert set(reasons) >= {
            EscalationReason.LOW_FIELD_CONFIDENCE,
            EscalationReason.ALTERNATIVES_PRESENT,
            EscalationReason.MULTI_SCRIPT,
            EscalationReason.DETERMINISTIC_CONFLICT,
        }


class TestStructure:
    def test_happy_path(self, cfg: Config, engine: InMemorySearchEngine) -> None:
        model = FakeModel(good_model_json(), name="cheap")
        r = structure(
            raw_text="x",
            deterministic=DETERMINISTIC,
            candidates=cands(engine, "LMK#DL#SHIV", "LMK#DL#GUPTA"),
            cfg=cfg,
            cheap_model=model,
        )
        assert not r.failed and r.model_used == "cheap" and r.retries == 0
        assert len(model.calls) == 1

    def test_one_retry_on_bad_json_then_success(
        self, cfg: Config, engine: InMemorySearchEngine
    ) -> None:
        model = FakeModel("not json", good_model_json(), name="cheap")
        r = structure(
            raw_text="x",
            deterministic=DETERMINISTIC,
            candidates=[],
            cfg=cfg,
            cheap_model=model,
        )
        assert not r.failed and r.retries == 1
        assert len(model.calls) == 2
        assert model.calls[1][1].endswith(STRICTER_REMINDER)

    def test_gives_up_after_one_retry(self, cfg: Config) -> None:
        """Never loop on a model."""
        model = FakeModel("garbage", "still garbage", "would succeed", name="cheap")
        r = structure(
            raw_text="x",
            deterministic=DETERMINISTIC,
            candidates=[],
            cfg=cfg,
            cheap_model=model,
        )
        assert r.failed and not r.unavailable
        assert len(model.calls) == 2  # not three
        assert EscalationReason.INVALID_JSON in r.escalation_reasons

    def test_unreachable_model_is_marked_unavailable(self, cfg: Config) -> None:
        model = FakeModel(ProviderUnavailable("bedrock down"))
        r = structure(
            raw_text="x",
            deterministic=DETERMINISTIC,
            candidates=[],
            cfg=cfg,
            cheap_model=model,
        )
        assert r.failed and r.unavailable

    def test_escalates_to_strong_model(self, cfg: Config) -> None:
        cheap = FakeModel(
            good_model_json(field_confidence={"building": 0.1}), name="cheap"
        )
        strong = FakeModel(
            good_model_json(field_confidence={"building": 0.99}), name="strong"
        )
        r = structure(
            raw_text="x",
            deterministic=DETERMINISTIC,
            candidates=[],
            cfg=cfg,
            cheap_model=cheap,
            strong_model=strong,
        )
        assert r.escalated and r.model_used == "strong"
        assert EscalationReason.LOW_FIELD_CONFIDENCE in r.escalation_reasons
        assert len(strong.calls) == 1

    def test_escalation_failure_keeps_cheap_answer(self, cfg: Config) -> None:
        cheap = FakeModel(
            good_model_json(field_confidence={"building": 0.1}), name="cheap"
        )
        strong = FakeModel(ProviderUnavailable("throttled"), name="strong")
        r = structure(
            raw_text="x",
            deterministic=DETERMINISTIC,
            candidates=[],
            cfg=cfg,
            cheap_model=cheap,
            strong_model=strong,
        )
        assert not r.failed and not r.escalated and r.model_used == "cheap"
        assert any("escalation to the strong model failed" in e for e in r.evidence)

    def test_multi_script_escalates(self, cfg: Config) -> None:
        cheap = FakeModel(good_model_json(), name="cheap")
        strong = FakeModel(good_model_json(), name="strong")
        r = structure(
            raw_text="x",
            deterministic=DETERMINISTIC,
            candidates=[],
            cfg=cfg,
            cheap_model=cheap,
            strong_model=strong,
            multi_script=True,
        )
        assert r.escalated and EscalationReason.MULTI_SCRIPT in r.escalation_reasons

    def test_escalation_can_be_disabled(self, cfg: Config) -> None:
        cheap = FakeModel(good_model_json(), name="cheap")
        strong = FakeModel(good_model_json(), name="strong")
        r = structure(
            raw_text="x",
            deterministic=DETERMINISTIC,
            candidates=[],
            cfg=cfg,
            cheap_model=cheap,
            strong_model=strong,
            multi_script=True,
            allow_escalation=False,
        )
        assert not r.escalated and strong.calls == []
        assert any("not performed" in e for e in r.evidence)
