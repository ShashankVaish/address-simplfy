"""The learning loop: what a confirmed delivery teaches the landmark graph.

Three write paths feed the graph (ARCHITECTURE section 5). This module is the
first and most important: a rider taps *Delivered*, and every landmark that
resolution used becomes a little more trustworthy and a little more accurate.

    observation_count += 1
    coordinate      <- EMA nudge toward the confirmed point
    aliases         <- the customer's spelling, if new
    confidence      <- rises with corroboration, capped

Why an exponential moving average and not a straight replace: a single GPS fix
at a doorstep can be 30 m off, or 300 m off under a tin roof. Replacing the
coordinate with every confirmation would let one bad reading move a landmark
that forty good ones had placed. An EMA with a weight that *shrinks* as
observations accumulate lets early confirmations correct a cold entry quickly
and late ones barely move a warm one -- which is what "learning" should mean.

Everything here is pure over a `SearchEngine`, so it runs identically against
OpenSearch in the learner Lambda and against the in-memory engine in tests and
in the learning-curve simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from patasetu.digipin import encode as digipin_encode
from patasetu.gazetteer import haversine_m
from patasetu.models import LandmarkRecord

# Confirmations further than this from the landmark's current coordinate are
# rejected as a bad fix rather than averaged in. A doorstep "behind Shiv
# Mandir" is within a couple of hundred metres of the temple; one 4 km away is
# a wrong tap or a wrong match, and either way not evidence about the temple.
MAX_NUDGE_DISTANCE_M = 400.0

# Floor on the EMA weight: even a landmark seen 500 times still moves a little,
# so a genuinely relocated shop is eventually followed.
MIN_ALPHA = 0.02

# Confirmation raises confidence by this much, capped below 1.0: the graph is
# evidence, never certainty.
CONFIDENCE_STEP = 0.05
CONFIDENCE_CAP = 0.97


@dataclass
class Update:
    """What one confirmation did to one landmark. Returned for the audit log."""

    landmark_id: str
    before_observations: int
    after_observations: int
    moved_m: float
    alias_added: str | None = None
    rejected_reason: str | None = None

    @property
    def applied(self) -> bool:
        return self.rejected_reason is None


@dataclass
class LearningEvent:
    """A confirmed delivery, as the learner receives it."""

    order_id: str
    landmark_ids: list[str]
    confirmed_lat: float
    confirmed_lng: float
    # How the customer wrote each landmark, keyed by id, for alias learning.
    spellings: dict[str, str] = field(default_factory=dict)
    # GPS accuracy reported by the device, if any; a poor fix nudges less.
    accuracy_m: float | None = None


def ema_alpha(observations: int) -> float:
    """Weight given to the new point. Large when cold, small when warm."""
    return max(MIN_ALPHA, 1.0 / (observations + 1))


def nudge(
    record: LandmarkRecord,
    *,
    lat: float,
    lng: float,
    accuracy_m: float | None = None,
) -> tuple[LandmarkRecord, float]:
    """Move the coordinate toward the confirmed point. Returns (record, metres moved)."""
    alpha = ema_alpha(record.observation_count)
    if accuracy_m is not None and accuracy_m > 50.0:
        # A poor fix earns less influence, scaled so 100 m accuracy halves it.
        alpha *= 50.0 / accuracy_m
    new_lat = record.lat + alpha * (lat - record.lat)
    new_lng = record.lng + alpha * (lng - record.lng)
    moved = haversine_m(record.lat, record.lng, new_lat, new_lng)
    updated = record.model_copy(
        update={
            "lat": round(new_lat, 6),
            "lng": round(new_lng, 6),
            "digipin_cell": digipin_encode(new_lat, new_lng),
        }
    )
    return updated, moved


def learn(engine: Any, event: LearningEvent) -> list[Update]:
    """Apply one confirmed delivery to every landmark it involved."""
    updates: list[Update] = []
    now = datetime.now(UTC).isoformat()

    for landmark_id in event.landmark_ids:
        record = engine.get(landmark_id)
        if record is None:
            updates.append(
                Update(landmark_id, 0, 0, 0.0, rejected_reason="landmark not in graph")
            )
            continue

        distance = haversine_m(
            record.lat, record.lng, event.confirmed_lat, event.confirmed_lng
        )
        if distance > MAX_NUDGE_DISTANCE_M:
            # Still counts as an observation of the *landmark* being referenced,
            # but its coordinate is not evidence about the landmark's position.
            updates.append(
                Update(
                    landmark_id,
                    record.observation_count,
                    record.observation_count,
                    0.0,
                    rejected_reason=(
                        f"confirmed point is {distance:.0f} m from the landmark; "
                        f"beyond {MAX_NUDGE_DISTANCE_M:.0f} m the fix is not trusted"
                    ),
                )
            )
            continue

        before = record.observation_count
        moved_record, moved = nudge(
            record,
            lat=event.confirmed_lat,
            lng=event.confirmed_lng,
            accuracy_m=event.accuracy_m,
        )

        alias_added: str | None = None
        spelling = (event.spellings.get(landmark_id) or "").strip()
        known = {a.casefold() for a in moved_record.aliases} | {
            moved_record.canonical_name.casefold()
        }
        if spelling and spelling.casefold() not in known:
            alias_added = spelling
            moved_record = moved_record.model_copy(
                update={"aliases": [*moved_record.aliases, spelling]}
            )

        moved_record = moved_record.model_copy(
            update={
                "observation_count": before + 1,
                "confidence": min(
                    CONFIDENCE_CAP, moved_record.confidence + CONFIDENCE_STEP
                ),
                "last_seen": now,
            }
        )
        engine.upsert([moved_record])
        updates.append(
            Update(landmark_id, before, before + 1, moved, alias_added=alias_added)
        )

    return updates
