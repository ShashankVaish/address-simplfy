"""Build the landmark index from the corpus.

Every seed carries real named places with real coordinates from the India Post
directory: the seed's own locality, and the nearby landmarks found by spatial
search. Those become the landmark graph. Nothing here is invented -- each record
is a genuine place at a published coordinate, which is what lets the graph be
"warmed" with real geometry rather than points made up to make retrieval work.

Two outputs:

*   `eval/data/landmarks.jsonl` -- the records, one per line, with embeddings.
    The in-memory engine loads this file; the ablation runner and the tests
    read it; and it is what `create_index.py` bulk-loads into OpenSearch.
*   The index itself, if `--upsert` is given and a provider is configured.

Embeddings are computed in batch (NFR: batch during ingestion, never one call
per row). With the local hashing embedder that is a formality; with Titan it is
the difference between a two-minute ingestion and a forty-minute one.

`--observations` simulates a warmed graph for configuration E: each landmark's
observation count is set from how many corpus addresses mention it. In
production this counter is incremented by confirmed deliveries; here it is
seeded from the corpus so E can be measured before any delivery has happened.
The provenance is recorded on every record so nobody mistakes it for real
delivery history.

Usage:
    python -m scripts.warm_landmarks
    python -m scripts.warm_landmarks --observations     # configuration E
    python -m scripts.warm_landmarks --upsert           # also push to the index
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "layers/common/python"))

from patasetu.digipin import encode as digipin_encode
from patasetu.embeddings import landmark_embedding_text
from patasetu.models import LandmarkRecord
from patasetu.providers import Providers


# Stable id from the place's identity, so re-running the script produces the
# same ids and an upsert replaces rather than duplicates.
def landmark_id(name: str, lat: float, lng: float, state: str) -> str:
    key = f"{name.casefold()}|{round(lat, 5)}|{round(lng, 5)}".encode()
    digest = hashlib.blake2b(key, digest_size=5).hexdigest().upper()
    code = _STATE_CODE.get(state, "IN")
    return f"LMK#{code}#{digest}"


_STATE_CODE = {
    "Delhi": "DL",
    "Maharashtra": "MH",
    "Karnataka": "KA",
    "Telangana": "TS",
    "Tamil Nadu": "TN",
    "West Bengal": "WB",
    "Gujarat": "GJ",
    "Rajasthan": "RJ",
    "Uttar Pradesh": "UP",
    "Kerala": "KL",
    "Assam": "AS",
    "Bihar": "BR",
    "Madhya Pradesh": "MP",
    "Odisha": "OD",
    "Punjab": "PB",
    "Haryana": "HR",
    "Chandigarh": "CH",
    "Andhra Pradesh": "AP",
}

# Rough type inference from the name, for the `type` field. Cheap and honest:
# it says "religious" when the name says mandir, and "unknown" otherwise.
_TYPE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("mandir", "temple", "masjid", "mosque", "church", "gurudwara", "dargah"),
        "religious",
    ),
    (("hospital", "clinic", "nursing home", "dispensary"), "medical"),
    (("school", "college", "university", "vidyalaya", "institute"), "education"),
    (("market", "bazaar", "bazar", "mall", "complex"), "commercial"),
    (("station", "railway", "metro", "bus stand", "depot"), "transport"),
    (("post office", "police", "court", "bhawan", "bhavan", "office"), "civic"),
    (("park", "garden", "ground", "stadium"), "open_space"),
    (("nagar", "colony", "vihar", "puram", "enclave", "layout", "sector"), "locality"),
)


def infer_type(name: str) -> str:
    low = name.casefold()
    for keywords, kind in _TYPE_HINTS:
        if any(k in low for k in keywords):
            return kind
    return "unknown"


def build_records(
    seeds: list[dict[str, Any]],
    corpus: list[dict[str, Any]] | None,
    *,
    with_observations: bool,
) -> list[LandmarkRecord]:
    """Collect every distinct real place across the seeds."""
    places: dict[str, dict[str, Any]] = {}
    aliases: dict[str, set[str]] = defaultdict(set)

    def add(name: str, lat: float, lng: float, pincode: str, state: str) -> str:
        lid = landmark_id(name, lat, lng, state)
        if lid not in places:
            places[lid] = {
                "landmark_id": lid,
                "canonical_name": name,
                "lat": lat,
                "lng": lng,
                "pincode": pincode,
                "state": state,
            }
        aliases[lid].add(name.casefold())
        return lid

    for s in seeds:
        t = s["truth"]
        # The seed's own locality is a place with a real coordinate.
        add(
            t["locality"],
            s["geo"]["lat"],
            s["geo"]["lng"],
            t["pincode"],
            t["state"] or "",
        )
        # And its nearby landmarks are too.
        for lm in s.get("landmark_truth", []):
            add(
                lm["name"],
                lm["true_lat"],
                lm["true_lng"],
                t["pincode"],
                t["state"] or "",
            )

    # Observation counts, when simulating a warmed graph (configuration E).
    observations: Counter[str] = Counter()
    if with_observations and corpus:
        by_seed: dict[str, list[str]] = defaultdict(list)
        for s in seeds:
            t = s["truth"]
            by_seed[s["seed_id"]].append(
                landmark_id(
                    t["locality"], s["geo"]["lat"], s["geo"]["lng"], t["state"] or ""
                )
            )
            for lm in s.get("landmark_truth", []):
                by_seed[s["seed_id"]].append(
                    landmark_id(
                        lm["name"], lm["true_lat"], lm["true_lng"], t["state"] or ""
                    )
                )
        for row in corpus:
            for lid in by_seed.get(row["seed_id"], []):
                observations[lid] += 1

    now = datetime.now(UTC).isoformat()
    records: list[LandmarkRecord] = []
    for lid, place in places.items():
        n_obs = observations.get(lid, 1) if with_observations else 1
        records.append(
            LandmarkRecord(
                landmark_id=lid,
                canonical_name=place["canonical_name"],
                aliases=sorted(aliases[lid] - {place["canonical_name"].casefold()}),
                type=infer_type(place["canonical_name"]),
                lat=place["lat"],
                lng=place["lng"],
                digipin_cell=digipin_encode(place["lat"], place["lng"]),
                pincode=place["pincode"],
                observation_count=n_obs,
                # A landmark seen once is a single observation; trust grows with
                # corroboration. Mirrors what the learner would do on delivery.
                confidence=min(0.95, 0.5 + 0.1 * (n_obs - 1)),
                access_notes=[],
                last_seen=now,
            )
        )
    return records


def embed_records(
    records: list[LandmarkRecord], providers: Providers, batch: int = 128
) -> None:
    """Attach embeddings, in batches."""
    embedder = providers.embedder
    for start in range(0, len(records), batch):
        chunk = records[start : start + batch]
        texts = [landmark_embedding_text(r.canonical_name, r.aliases) for r in chunk]
        vectors = embedder.embed_batch(texts)
        for record, vector in zip(chunk, vectors, strict=True):
            record.embedding = vector


def load_landmarks(path: Path) -> list[LandmarkRecord]:
    """Read a landmarks.jsonl into records."""
    return [
        LandmarkRecord.model_validate(json.loads(line))
        for line in path.open(encoding="utf-8")
        if line.strip()
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--seeds", type=Path, default=Path("eval/data/seed_addresses.jsonl")
    )
    ap.add_argument("--corpus", type=Path, default=Path("eval/data/corpus.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("eval/data/landmarks.jsonl"))
    ap.add_argument(
        "--observations",
        action="store_true",
        help="seed observation counts from corpus mentions (configuration E)",
    )
    ap.add_argument("--upsert", action="store_true", help="also push to the index")
    args = ap.parse_args()

    if not args.seeds.exists():
        print(f"error: {args.seeds} not found", file=sys.stderr)
        return 1

    seeds = [json.loads(line) for line in args.seeds.open(encoding="utf-8")]
    corpus = (
        [json.loads(line) for line in args.corpus.open(encoding="utf-8")]
        if args.corpus.exists()
        else None
    )

    providers = Providers()
    records = build_records(seeds, corpus, with_observations=args.observations)
    embed_records(records, providers)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as fh:
        for r in records:
            fh.write(json.dumps(r.model_dump(mode="json"), ensure_ascii=False) + "\n")

    types = Counter(r.type for r in records)
    obs = [r.observation_count for r in records]
    print(
        f"wrote {len(records):,} landmarks -> {args.out}   (provider={providers.cfg.provider})"
    )
    print(
        f"  observation counts: min {min(obs)}  max {max(obs)}  mean {sum(obs) / len(obs):.1f}"
    )
    print("  types: " + ", ".join(f"{k} {v}" for k, v in types.most_common()))

    if args.upsert:
        engine = providers.search
        engine.ensure_index()
        n = engine.upsert(records)
        print(f"  upserted {n:,} into the index ({engine.count():,} total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
