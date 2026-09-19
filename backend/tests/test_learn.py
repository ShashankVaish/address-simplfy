"""The learning loop: observation counts, EMA nudges, aliases, bad-fix rejection."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from patasetu import learn, store
from patasetu.gazetteer import haversine_m
from patasetu.search import InMemorySearchEngine
from patasetu.store import InMemoryStore

SHIV = (28.6524, 77.1206)


def event(
    *ids: str, lat: float = 28.6526, lng: float = 77.1208, **kw: Any
) -> learn.LearningEvent:
    return learn.LearningEvent(
        order_id="O1",
        landmark_ids=list(ids),
        confirmed_lat=lat,
        confirmed_lng=lng,
        **kw,
    )


class TestAlpha:
    def test_cold_landmark_moves_a_lot_warm_one_barely(self) -> None:
        assert learn.ema_alpha(0) == 1.0
        assert learn.ema_alpha(1) == 0.5
        assert learn.ema_alpha(9) == 0.1
        assert learn.ema_alpha(10_000) == learn.MIN_ALPHA


class TestLearn:
    def test_confirmation_increments_and_nudges(
        self, engine: InMemorySearchEngine
    ) -> None:
        before = engine.get("LMK#DL#SHIV")
        assert before.observation_count == 47
        updates = learn.learn(engine, event("LMK#DL#SHIV"))
        after = engine.get("LMK#DL#SHIV")
        assert updates[0].applied
        assert after.observation_count == 48
        assert after.confidence > before.confidence
        # 47 observations: the nudge is small but real, toward the fix.
        assert 0.0 < updates[0].moved_m < 2.0
        assert haversine_m(after.lat, after.lng, 28.6526, 77.1208) < haversine_m(
            before.lat, before.lng, 28.6526, 77.1208
        )
        assert after.digipin_cell is not None

    def test_cold_landmark_jumps_to_the_first_confirmation(
        self, engine: InMemorySearchEngine
    ) -> None:
        engine.upsert(
            [engine.get("LMK#DL#SHIV").model_copy(update={"observation_count": 0})]
        )
        learn.learn(engine, event("LMK#DL#SHIV"))
        after = engine.get("LMK#DL#SHIV")
        assert (after.lat, after.lng) == (28.6526, 77.1208)

    def test_one_bad_fix_cannot_move_a_warm_landmark_far(
        self, engine: InMemorySearchEngine
    ) -> None:
        """The reason for an EMA rather than a replace."""
        learn.learn(
            engine, event("LMK#DL#SHIV", lat=SHIV[0] + 0.003, lng=SHIV[1])
        )  # ~330 m off, accepted
        after = engine.get("LMK#DL#SHIV")
        assert haversine_m(SHIV[0], SHIV[1], after.lat, after.lng) < 10

    def test_fix_far_from_landmark_is_rejected(
        self, engine: InMemorySearchEngine
    ) -> None:
        updates = learn.learn(
            engine, event("LMK#DL#SHIV", lat=28.70, lng=77.20)
        )  # ~9 km
        assert not updates[0].applied and "not trusted" in updates[0].rejected_reason
        assert engine.get("LMK#DL#SHIV").observation_count == 47  # unchanged

    def test_poor_gps_accuracy_nudges_less(self, engine: InMemorySearchEngine) -> None:
        engine.upsert(
            [engine.get("LMK#DL#SHIV").model_copy(update={"observation_count": 3})]
        )
        good = learn.learn(engine, event("LMK#DL#SHIV", accuracy_m=10))[0].moved_m
        engine.upsert(
            [
                engine.get("LMK#DL#SHIV").model_copy(
                    update={"observation_count": 3, "lat": SHIV[0], "lng": SHIV[1]}
                )
            ]
        )
        poor = learn.learn(engine, event("LMK#DL#SHIV", accuracy_m=200))[0].moved_m
        assert poor < good

    def test_new_spelling_becomes_an_alias(self, engine: InMemorySearchEngine) -> None:
        updates = learn.learn(
            engine, event("LMK#DL#SHIV", spellings={"LMK#DL#SHIV": "Shiv Mandhir"})
        )
        assert updates[0].alias_added == "Shiv Mandhir"
        assert "Shiv Mandhir" in engine.get("LMK#DL#SHIV").aliases
        # And it is now searchable.
        assert engine.bm25("mandhir", ["LMK#DL#SHIV"])

    def test_known_spelling_is_not_duplicated(
        self, engine: InMemorySearchEngine
    ) -> None:
        updates = learn.learn(
            engine, event("LMK#DL#SHIV", spellings={"LMK#DL#SHIV": "shiv temple"})
        )
        assert updates[0].alias_added is None

    def test_unknown_landmark_is_reported_not_created(
        self, engine: InMemorySearchEngine
    ) -> None:
        updates = learn.learn(engine, event("LMK#NOPE"))
        assert not updates[0].applied and "not in graph" in updates[0].rejected_reason

    def test_confidence_is_capped(self, engine: InMemorySearchEngine) -> None:
        for _ in range(20):
            learn.learn(engine, event("LMK#DL#SHIV"))
        assert engine.get("LMK#DL#SHIV").confidence <= learn.CONFIDENCE_CAP


def load_learner(kv: InMemoryStore, engine: InMemorySearchEngine) -> Any:
    path = Path(__file__).resolve().parents[1] / "functions" / "learner" / "app.py"
    spec = importlib.util.spec_from_file_location("learner_app", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.PROVIDERS.__dict__["store"] = kv
    module.PROVIDERS.__dict__["search"] = engine
    return module


class TestLearnerLambda:
    def _order(self, kv: InMemoryStore) -> None:
        store.record_resolution(
            kv,
            order_id="ORD-9",
            resolution={
                "status": "RESOLVED",
                "confidence": 0.9,
                "structured": {
                    "locality": "Ramesh Nagar",
                    "landmarks": [
                        {
                            "name": "Shiv Mandhir",
                            "relation": "behind",
                            "matched_id": "LMK#DL#SHIV",
                        }
                    ],
                },
                "geo": {"lat": 28.6524, "lng": 77.1206, "source": "landmark_graph"},
                "evidence": [],
            },
            correlation_id="c",
        )

    def test_delivery_confirmed_updates_the_graph_and_the_timeline(
        self, engine: InMemorySearchEngine
    ) -> None:
        kv = InMemoryStore()
        self._order(kv)
        app = load_learner(kv, engine)
        out = app.handler(
            {
                "detail-type": "DeliveryConfirmed",
                "detail": {
                    "order_id": "ORD-9",
                    "location": {"lat": 28.6526, "lng": 77.1208},
                    "accuracy_m": 8,
                },
            }
        )
        assert out == {"ok": True, "applied": 1, "updates": 1}
        assert engine.get("LMK#DL#SHIV").observation_count == 48
        assert "Shiv Mandhir" in engine.get("LMK#DL#SHIV").aliases
        events = kv.query_prefix(store.order_pk("ORD-9"), "EVENT#")
        assert events[-1]["event"] == "delivery_confirmed"

    def test_without_rider_fix_uses_the_resolved_point(
        self, engine: InMemorySearchEngine
    ) -> None:
        kv = InMemoryStore()
        self._order(kv)
        app = load_learner(kv, engine)
        out = app.handler(
            {
                "detail-type": "DeliveryConfirmed",
                "detail": json.dumps({"order_id": "ORD-9"}),
            }
        )
        assert out["ok"] and out["applied"] == 1

    def test_other_event_types_are_ignored(self, engine: InMemorySearchEngine) -> None:
        app = load_learner(InMemoryStore(), engine)
        assert (
            app.handler({"detail-type": "CaseReviewed", "detail": {"order_id": "x"}})[
                "ignored"
            ]
            == "CaseReviewed"
        )

    def test_unknown_order_is_skipped(self, engine: InMemorySearchEngine) -> None:
        app = load_learner(InMemoryStore(), engine)
        assert (
            app.handler(
                {"detail-type": "DeliveryConfirmed", "detail": {"order_id": "nope"}}
            )["ok"]
            is False
        )
