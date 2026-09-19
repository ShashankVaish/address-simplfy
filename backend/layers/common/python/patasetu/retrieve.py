"""S2 -- hybrid candidate retrieval and Reciprocal Rank Fusion.

Three signals come back from the index as three separate rankings. This module
fuses them into one candidate list and hands the top few to S3.

    score(d) = sum over signals i of  1 / (k + rank_i(d)),   k = 60

RRF rather than a tuned linear blend of the raw scores, for two reasons that
both matter on a four-day build:

*   **No score normalisation.** BM25 returns an unbounded relevance score,
    cosine returns [-1,1], and proximity returns metres. Blending those linearly
    requires normalising three incompatible scales, and the normalisation
    constants would need refitting every time the corpus changes. RRF consumes
    only *ranks*, so the scales never have to be reconciled.
*   **Graceful degradation.** An address with no pincode and no GPS hint has no
    geo signal; one whose embedder is unavailable has no vector signal. A linear
    blend would need a special case per missing signal, and would silently shift
    its effective weighting. RRF simply sums over whichever signals are present.

The cost of RRF is that it discards score *magnitude*: a BM25 score of 40 and
one of 4 both rank first and contribute identically. That is the right trade
here, where the ranking is reliable and the absolute scores are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from patasetu.config import Config
from patasetu.embeddings import cosine, is_zero
from patasetu.models import LandmarkRecord, Relation
from patasetu.providers import Providers, ProviderUnavailable
from patasetu.search import (
    SIGNAL_GEO,
    SIGNAL_LEXICAL,
    SIGNAL_VECTOR,
    tokenise,
)


@dataclass
class Candidate:
    """One fused landmark candidate, with its provenance.

    `signals` records which signals found it and at what rank. That is not
    debug detail: it is what the evidence list reports to the operator, and it
    is how we can say "the vector signal was the only one that found this"
    rather than just handing over a number.
    """

    landmark_id: str
    record: LandmarkRecord
    rrf_score: float
    signals: dict[str, int] = field(default_factory=dict)
    distance_m: float | None = None
    # How strongly the *text* names this landmark: the best `name_affinity`
    # against any phrase in the address, in [0,1]. Set by the pipeline after
    # matching. This -- not the RRF score -- is what S7 compares when deciding
    # whether two candidates are genuine rivals, because adjacent RRF ranks are
    # always numerically close (1/61 vs 1/62) whether or not the names are.
    affinity: float = 0.0

    @property
    def found_by(self) -> list[str]:
        return sorted(self.signals)

    @property
    def signal_count(self) -> int:
        return len(self.signals)


@dataclass
class RetrievalResult:
    """What S2 hands to S3 and S6."""

    candidates: list[Candidate]
    # Normalised [0,1] score of the best candidate, for the S6 feature.
    top_score: float = 0.0
    # Which signals produced any hits at all. A missing signal is reported so
    # confidence can be penalised for running degraded.
    signals_used: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.candidates


def reciprocal_rank_fusion(
    rankings: dict[str, list[tuple[str, float]]],
    *,
    k: int = 60,
    weights: dict[str, float] | None = None,
) -> list[tuple[str, float, dict[str, int]]]:
    """Fuse ranked lists into one. Returns (id, score, {signal: rank}).

    Ranks are 1-based, which is what the RRF formula assumes: a 0-based rank
    would give the top hit a contribution of 1/k instead of 1/(k+1), inflating
    first place relative to every other position.

    `weights` scales a signal's contribution. Defaults to 1.0 for each. It
    exists because proximity is weaker evidence than a name match -- being 200 m
    away does not identify a landmark on its own -- but the default keeps the
    fusion parameter-free unless a caller deliberately opts in.
    """
    if k <= 0:
        raise ValueError("RRF k must be positive")

    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}

    for signal, ranked in rankings.items():
        weight = (weights or {}).get(signal, 1.0)
        seen: set[str] = set()
        rank = 0
        for doc_id, _score in ranked:
            # An id repeated within one signal's list must not be counted twice;
            # its first (best) position is the one that counts.
            if doc_id in seen:
                continue
            seen.add(doc_id)
            rank += 1
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
            ranks.setdefault(doc_id, {})[signal] = rank

    fused = [(doc_id, score, ranks[doc_id]) for doc_id, score in scores.items()]
    # Tie-break on signal count then id: a candidate found by two signals is a
    # better bet than one found by a single signal at the same score, and the id
    # makes the order deterministic for the tests and for reproducible ablations.
    fused.sort(key=lambda t: (-t[1], -len(t[2]), t[0]))
    return fused


def normalise_rrf(score: float, n_signals: int, k: int) -> float:
    """Map an RRF score into [0,1] for use as a confidence feature.

    The theoretical maximum is every signal ranking the candidate first, i.e.
    `n_signals / (k + 1)`. Dividing by that makes the feature comparable across
    addresses that had different numbers of signals available -- without it, an
    address with no geo signal would look less confident purely because one term
    was missing from the sum.
    """
    if n_signals <= 0:
        return 0.0
    best_possible = n_signals / (k + 1)
    return max(0.0, min(1.0, score / best_possible)) if best_possible else 0.0


# Signal weights. Name evidence outranks mere proximity: inside a 3 km circle
# there are many landmarks and only one of them is the one named.
DEFAULT_WEIGHTS: dict[str, float] = {
    SIGNAL_LEXICAL: 1.0,
    SIGNAL_VECTOR: 1.0,
    SIGNAL_GEO: 0.5,
}


def build_query_text(landmark_names: list[str], retrieval_text: str) -> str:
    """The text S2 searches with.

    Prefers the extracted landmark phrases over the whole address. Searching the
    full address text pollutes the query with the house number, the pincode and
    the city, all of which appear in no landmark name and which dilute both the
    BM25 scoring and the embedding. Falls back to the full retrieval text when
    S1 found no landmark phrase at all.
    """
    names = [n.strip() for n in landmark_names if n and n.strip()]
    return " ".join(names) if names else retrieval_text


def retrieve(
    *,
    landmark_names: list[str],
    retrieval_text: str,
    centre: tuple[float, float] | None,
    cfg: Config,
    providers: Providers,
    use_vector: bool = True,
    use_geo: bool = True,
    radius_m: float | None = None,
) -> RetrievalResult:
    """Run S2.

    `use_vector` and `use_geo` exist for the ablation: configuration C is this
    same code path with both switched off, so the measured gap from C to D is
    attributable to those two signals and nothing else.

    `radius_m` overrides the configured geo radius. The caller knows what kind
    of centre it passed: a GPS hint deserves the tight default, a pincode
    centroid needs a radius scaled to the pincode's size.
    """
    result = RetrievalResult(candidates=[])
    radius = radius_m if radius_m is not None else cfg.retrieval.geo_radius_m
    query_text = build_query_text(landmark_names, retrieval_text)
    if not query_text.strip():
        result.evidence.append("S2 skipped: nothing to search for")
        return result

    engine = providers.search

    # --- query vector ------------------------------------------------------
    query_vector: list[float] | None = None
    if use_vector:
        try:
            query_vector = providers.embedder.embed(query_text)
            if is_zero(query_vector):
                query_vector = None
                result.degraded.append("vector signal: query embedded to zero")
        except ProviderUnavailable as exc:
            # Degrade to lexical-only and say so, rather than failing the
            # request. A landmark found by name is still a landmark found.
            query_vector = None
            result.degraded.append(f"vector signal unavailable: {exc}")

    effective_centre = centre if use_geo else None
    if use_geo and centre is None:
        result.degraded.append(
            "geo signal unavailable: no validated pincode and no GPS hint, "
            "so candidates are not geographically constrained"
        )

    # --- one round trip ----------------------------------------------------
    try:
        rankings = engine.hybrid_search(
            query_text=query_text,
            query_vector=query_vector,
            centre=effective_centre,
            radius_m=radius,
            size=cfg.retrieval.per_signal_size,
        )
    except ProviderUnavailable as exc:
        result.degraded.append(f"S2 unavailable, proceeding without retrieval: {exc}")
        result.evidence.append(f"retrieval skipped: {exc}")
        return result

    if not use_vector:
        rankings.pop(SIGNAL_VECTOR, None)
    if not use_geo:
        rankings.pop(SIGNAL_GEO, None)

    populated = {name: hits for name, hits in rankings.items() if hits}
    result.signals_used = sorted(populated)

    if not populated:
        result.evidence.append(
            f"S2 found no landmark candidates for {query_text!r} within {radius:.0f} m"
        )
        return result

    fused = reciprocal_rank_fusion(
        populated, k=cfg.retrieval.rrf_k, weights=DEFAULT_WEIGHTS
    )

    # --- materialise the top candidates ------------------------------------
    distances: dict[str, float] = {}
    if effective_centre is not None and hasattr(engine, "distances_from"):
        distances = engine.distances_from(
            effective_centre, [doc_id for doc_id, _, _ in fused]
        )

    # Materialise the whole fused list, not just the top few. The cap in
    # `candidates_to_model` is a *prompt* budget and is applied where the prompt
    # is built. Applying it here let the proximity signal fill every slot with
    # nearby-but-unnamed places, and the landmark the text actually named --
    # ranked first by BM25 but pushed to ninth by two weak signals -- was gone
    # before the affinity matcher could see it. Measured: it cost R2 a full
    # kilometre of median geocode error relative to BM25 alone.
    for doc_id, score, ranks in fused:
        record = engine.get(doc_id)
        if record is None:
            # The index returned an id we cannot fetch. Skip it rather than
            # fabricate a record; a candidate we cannot describe is useless to
            # S3 and dangerous to S4.
            result.degraded.append(f"index returned unknown id {doc_id}")
            continue
        result.candidates.append(
            Candidate(
                landmark_id=doc_id,
                record=record,
                rrf_score=score,
                signals=ranks,
                distance_m=distances.get(doc_id),
            )
        )

    if result.candidates:
        result.top_score = normalise_rrf(
            result.candidates[0].rrf_score,
            n_signals=len(populated),
            k=cfg.retrieval.rrf_k,
        )
        best = result.candidates[0]
        located = f" at {best.distance_m:.0f} m" if best.distance_m is not None else ""
        result.evidence.append(
            f"landmark '{best.record.canonical_name}' matched "
            f"{best.landmark_id}{located} "
            f"(fused {result.top_score:.2f} from {', '.join(best.found_by)}; "
            f"seen {best.record.observation_count}x)"
        )
        if len(result.candidates) > 1:
            result.evidence.append(
                f"{len(result.candidates)} candidates retrieved from "
                f"{len(populated)} signal(s): {', '.join(result.signals_used)}"
            )

    for note in result.degraded:
        result.evidence.append(f"degraded: {note}")

    return result


# Minimum name affinity for a phrase to claim a candidate. Below this the
# landmark stays unmatched, which is the correct answer: retrieval returned
# *something* nearby, but nothing that is plausibly the place named.
MIN_MATCH_AFFINITY = 0.35


def name_affinity(phrase: str, record: LandmarkRecord, embedder: object) -> float:
    """How much a landmark phrase looks like a specific landmark's name.

    Retrieval generates candidates from all the phrases at once, in one round
    trip. This is the separate question of *which* phrase corresponds to
    *which* candidate, and it is answered locally -- token overlap plus vector
    similarity against the candidate's own name and aliases -- so it costs no
    additional round trip.
    """
    phrase_tokens = set(tokenise(phrase))
    if not phrase_tokens:
        return 0.0

    best_lexical = 0.0
    for name in [record.canonical_name, *record.aliases]:
        name_tokens = set(tokenise(name))
        if not name_tokens:
            continue
        overlap = len(phrase_tokens & name_tokens)
        if overlap:
            # Jaccard-style, so a one-word phrase matching a one-word name
            # scores higher than it matching a five-word name.
            best_lexical = max(best_lexical, overlap / len(phrase_tokens | name_tokens))

    vector_similarity = 0.0
    embed = getattr(embedder, "embed", None)
    if callable(embed) and record.embedding:
        try:
            vector_similarity = max(0.0, cosine(embed(phrase), record.embedding))
        except Exception:
            # Affinity is a heuristic; an embedder failure must not fail the
            # request. Lexical overlap alone still gives a usable answer.
            vector_similarity = 0.0

    # Lexical overlap is the stronger signal for proper nouns; the vector
    # rescues misspellings and abbreviations that share no whole token.
    return max(best_lexical, 0.85 * vector_similarity)


def attach_matches(
    landmark_names: list[tuple[str, Relation]],
    result: RetrievalResult,
    embedder: object | None = None,
) -> list[dict[str, object]]:
    """Pair each S1 landmark phrase with the candidate it actually names.

    Assignment is by measured affinity, globally greedy: every (phrase,
    candidate) pair is scored, the strongest pair is fixed first, and both are
    then removed from consideration. Two properties follow, and both matter:

    *   **The right phrase gets the right landmark.** Assigning candidates in
        retrieval order instead would hand "behind shiv mandir" whichever
        candidate happened to rank first overall -- quite possibly the Gupta
        store from the *other* phrase in the same address.
    *   **One-to-one.** Two phrases cannot claim the same landmark, which would
        double-count one piece of evidence in S6's landmark-match feature.

    A phrase whose best affinity is below `MIN_MATCH_AFFINITY` keeps
    `matched_id: None` and its own raw name (FR-02, FR-12). An unmatched
    landmark is not a failure to hide: it is evidence for the operator, and it
    is what seeds the graph next time someone writes this address.
    """
    out: list[dict[str, object]] = [
        {
            # Title-cased for consistency with the S1 projection, so an
            # unmatched landmark reads the same whichever path produced it.
            "name": name.title(),
            "relation": relation,
            "matched_id": None,
            "match_score": None,
            "distance_m": None,
        }
        for name, relation in landmark_names
    ]
    if not result.candidates or not landmark_names:
        return out

    pairs: list[tuple[float, int, str]] = []
    for i, (phrase, _relation) in enumerate(landmark_names):
        for candidate in result.candidates:
            affinity = name_affinity(phrase, candidate.record, embedder)
            # Record the strongest affinity seen for each candidate, for S7.
            candidate.affinity = max(candidate.affinity, affinity)
            if affinity >= MIN_MATCH_AFFINITY:
                pairs.append((affinity, i, candidate.landmark_id))

    # Strongest pair first; id breaks ties deterministically.
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))
    by_id = {c.landmark_id: c for c in result.candidates}
    top_rrf = result.candidates[0].rrf_score or 1.0

    used_phrases: set[int] = set()
    used_ids: set[str] = set()
    for affinity, i, landmark_id in pairs:
        if i in used_phrases or landmark_id in used_ids:
            continue
        used_phrases.add(i)
        used_ids.add(landmark_id)

        candidate = by_id[landmark_id]
        out[i] = {
            # The canonical name replaces the customer's phrasing once we are
            # confident which landmark it is -- that is the point of having a
            # graph. The raw phrase survives in the evidence list.
            "name": candidate.record.canonical_name,
            "relation": landmark_names[i][1],
            "matched_id": landmark_id,
            "match_score": round(
                min(1.0, (candidate.rrf_score / top_rrf) * result.top_score * affinity),
                4,
            ),
            "distance_m": candidate.distance_m,
        }
    return out
