"""S3 -- LLM structuring with constrained output, and the model cascade.

This is the only stage where a model sees the text, and it sees it *with* the
deterministic fields and the retrieved candidates already in the prompt. Its job
is narrow: assign the remaining spans to fields, attach landmark relations, and
pick from a supplied candidate list. That framing is what makes a small cheap
model sufficient for the large majority of addresses.

The cascade:

    S1 complete + validated pincode + no landmarks   ->  no model at all, 0 cost
    otherwise                                        ->  Nova Lite
    escalate on any of:                              ->  Claude
      * a per-field confidence below threshold
      * a field conflicting with the deterministic parse
      * multi-script input
      * a non-empty alternatives list

Escalation is measured and reported, because "94% of addresses never touch the
expensive model" is only a claim if the 94% is a number someone computed.

Two hard rules about failure. A model that returns invalid JSON gets exactly one
retry with a stricter reminder, and then we fall back to the deterministic
result -- never a loop, never a partial parse of broken output. And a model
field that contradicts a gazetteer-validated pincode is recorded as a conflict,
not applied: the validated data wins.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from patasetu.config import PROMPT_DIR, Config
from patasetu.models import Relation
from patasetu.providers import ProviderUnavailable
from patasetu.retrieve import Candidate

_PROMPT_CACHE: dict[str, str] = {}

# The prompt ships inside the package (`patasetu/prompts/`), so a Lambda layer,
# a test and a script all load the identical text -- a prompt that differs
# between the evaluation and production makes the evaluation worthless.
_PROMPT_DIR = PROMPT_DIR


class EscalationReason(StrEnum):
    LOW_FIELD_CONFIDENCE = "low_field_confidence"
    DETERMINISTIC_CONFLICT = "deterministic_conflict"
    MULTI_SCRIPT = "multi_script"
    ALTERNATIVES_PRESENT = "alternatives_present"
    INVALID_JSON = "invalid_json"


# Below this per-field confidence from the cheap model, escalate.
FIELD_CONFIDENCE_FLOOR = 0.55

_VALID_RELATIONS = {r.value for r in Relation}

# Fields the model may set. Anything else it emits is dropped rather than
# trusted: a model inventing a "landmark_2" key must not silently become part of
# the response.
_ALLOWED_FIELDS = (
    "building",
    "street",
    "sub_locality",
    "locality",
    "city",
    "district",
    "state",
    "pincode",
)


def load_prompt(name: str = "structure.txt") -> str:
    """Load and cache a prompt file."""
    if name not in _PROMPT_CACHE:
        path = os.path.normpath(os.path.join(_PROMPT_DIR, name))
        try:
            with open(path, encoding="utf-8") as fh:
                _PROMPT_CACHE[name] = fh.read()
        except OSError as exc:
            raise ProviderUnavailable(
                f"prompt {name} not found at {path}: {exc}"
            ) from exc
    return _PROMPT_CACHE[name]


@dataclass
class StructureResult:
    """What S3 produced, and how much it cost to get it."""

    fields: dict[str, str | None] = field(default_factory=dict)
    landmarks: list[dict[str, Any]] = field(default_factory=list)
    field_confidence: dict[str, float] = field(default_factory=dict)
    field_reason: dict[str, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    model_used: str | None = None
    escalated: bool = False
    escalation_reasons: list[str] = field(default_factory=list)
    retries: int = 0
    # True when the cascade decided no model was needed at all.
    skipped: bool = False
    # `failed`: the model answered but with unusable output (after a retry).
    # `unavailable`: the model could not be reached at all. The pipeline treats
    # these differently -- garbage degrades to the deterministic result, but an
    # unreachable provider means this configuration cannot honestly run.
    failed: bool = False
    unavailable: bool = False
    evidence: list[str] = field(default_factory=list)

    @property
    def self_confidence(self) -> float:
        """The model's mean self-reported confidence, a *feature* not an answer.

        LLMs are poorly calibrated and are confidently wrong in exactly the
        cases that matter here, so S6 weights this lightly and never uses it
        alone.
        """
        if not self.field_confidence:
            return 0.5
        values = list(self.field_confidence.values())
        return max(0.0, min(1.0, sum(values) / len(values)))


def extract_json(text: str) -> dict[str, Any]:
    """Pull one JSON object out of a model response.

    Models wrap JSON in code fences and prefix it with "Here is the JSON:" no
    matter how firmly the prompt forbids it. Rather than fail the request on a
    formatting habit, we find the outermost balanced braces. Anything that is
    still not parseable raises, and the caller retries once with a stricter
    reminder before giving up.
    """
    if not text or not text.strip():
        raise ValueError("empty model response")

    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())

    start = cleaned.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in response: {text[:120]!r}")

    # Brace matching that ignores braces inside strings, so an address
    # containing a brace cannot truncate the parse.
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : i + 1])

    raise ValueError("unbalanced JSON object in model response")


def build_user_prompt(
    *,
    raw_text: str,
    deterministic: dict[str, Any],
    candidates: list[Candidate],
) -> str:
    """Assemble the user half of the prompt.

    Candidates are rendered with their id, name, aliases and distance, and
    nothing else. Sending the embedding or the full record would waste tokens
    on data the model cannot use, and more candidates than a handful measurably
    hurts selection accuracy -- the list is truncated by the caller.
    """
    candidate_payload = [
        {
            "id": c.landmark_id,
            "name": c.record.canonical_name,
            "aliases": c.record.aliases[:4],
            "distance_m": round(c.distance_m) if c.distance_m is not None else None,
            "seen_times": c.record.observation_count,
        }
        for c in candidates
    ]
    settled = {k: v for k, v in deterministic.items() if v is not None}

    return (
        f"INPUT\n{raw_text}\n\n"
        f"DETERMINISTIC\n{json.dumps(settled, ensure_ascii=False)}\n\n"
        f"CANDIDATES\n{json.dumps(candidate_payload, ensure_ascii=False)}\n\n"
        f"Emit the JSON object now."
    )


STRICTER_REMINDER = (
    "\n\nYour previous response was not valid JSON. Emit ONLY the JSON object. "
    "The first character of your reply must be '{' and the last must be '}'. "
    "No code fences, no explanation."
)


def normalise_model_output(
    payload: dict[str, Any],
    *,
    deterministic: dict[str, Any],
    candidate_ids: set[str],
) -> StructureResult:
    """Validate and clean what the model returned.

    This function is where we stop trusting the model. Four things it does that
    a naive `StructuredAddress(**payload)` would not:

    *   **Drops unknown fields** rather than letting them through.
    *   **Rejects invented landmark ids.** An id not in the candidate list
        becomes null with the raw name kept, because a fabricated id would send
        S4 to a coordinate for a completely different place.
    *   **Refuses to overwrite settled fields.** A model rewriting a
        gazetteer-validated pincode is recorded as a conflict and ignored.
    *   **Coerces relations** to the allowed set, defaulting to "near".
    """
    result = StructureResult()

    for name in _ALLOWED_FIELDS:
        value = payload.get(name)
        if isinstance(value, str):
            value = value.strip() or None
        elif value is not None and not isinstance(value, str):
            value = str(value).strip() or None

        settled = deterministic.get(name)
        if settled is not None and value is not None and value != settled:
            # The validated parse wins, and the disagreement is reported rather
            # than averaged away (NFR-14).
            result.conflicts.append(
                f"model said {name}={value!r} but the deterministic parse has "
                f"{settled!r}; keeping the deterministic value"
            )
            # Keep the settled value in the result. Skipping this line dropped
            # the validated pincode from `fields` on every conflict, leaving a
            # gap where the one value we were sure of should have been.
            result.fields[name] = settled
            continue
        if settled is not None:
            result.fields[name] = settled
        elif value is not None:
            result.fields[name] = value

    for item in payload.get("landmarks") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        relation = (item.get("relation") or "near").strip().lower()
        if relation not in _VALID_RELATIONS:
            relation = "near"

        matched_id = item.get("matched_id")
        if matched_id is not None:
            matched_id = str(matched_id).strip() or None
        if matched_id is not None and matched_id not in candidate_ids:
            result.conflicts.append(
                f"model returned landmark id {matched_id!r}, which was not in the "
                f"candidate list; treating '{name}' as unmatched"
            )
            matched_id = None

        result.landmarks.append(
            {"name": name, "relation": relation, "matched_id": matched_id}
        )

    raw_conf = payload.get("field_confidence")
    if isinstance(raw_conf, dict):
        for key, value in raw_conf.items():
            if key in _ALLOWED_FIELDS:
                try:
                    result.field_confidence[key] = max(0.0, min(1.0, float(value)))
                except (TypeError, ValueError):
                    continue

    raw_reason = payload.get("field_reason")
    if isinstance(raw_reason, dict):
        result.field_reason = {
            k: str(v)[:200] for k, v in raw_reason.items() if k in _ALLOWED_FIELDS
        }

    for conflict in payload.get("conflicts") or []:
        if isinstance(conflict, str) and conflict.strip():
            result.conflicts.append(conflict.strip()[:300])

    for alt in payload.get("alternatives") or []:
        if isinstance(alt, dict) and alt.get("fields"):
            result.alternatives.append(
                {
                    "reason": str(alt.get("reason") or "")[:300],
                    "fields": {
                        k: v
                        for k, v in (alt.get("fields") or {}).items()
                        if k in _ALLOWED_FIELDS
                    },
                }
            )

    return result


def should_escalate(result: StructureResult, *, multi_script: bool) -> list[str]:
    """Decide whether the cheap model's answer needs the expensive one."""
    reasons: list[str] = []
    if result.conflicts:
        reasons.append(EscalationReason.DETERMINISTIC_CONFLICT.value)
    if result.alternatives:
        reasons.append(EscalationReason.ALTERNATIVES_PRESENT.value)
    if multi_script:
        reasons.append(EscalationReason.MULTI_SCRIPT.value)
    if result.field_confidence and min(result.field_confidence.values()) < (
        FIELD_CONFIDENCE_FLOOR
    ):
        reasons.append(EscalationReason.LOW_FIELD_CONFIDENCE.value)
    return reasons


class BedrockModel:
    """Amazon Bedrock via the Converse API.

    Converse rather than `invoke_model` because it presents one request shape
    across Nova and Claude. The cascade then needs no per-model request
    translation, which is the whole reason both models sit behind one API.
    """

    def __init__(self, model_id: str, region: str, temperature: float = 0.0) -> None:
        self.model_id = model_id
        self.name = model_id
        self.region = region
        self.temperature = temperature
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - boto3 ships in Lambda
                raise ProviderUnavailable("boto3 is required for BedrockModel") from exc
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        try:
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={
                    "maxTokens": max_tokens,
                    # Deterministic decoding: extraction, not composition. A
                    # non-zero temperature would also make the ablation
                    # irreproducible.
                    "temperature": self.temperature,
                },
            )
        except Exception as exc:
            raise ProviderUnavailable(f"Bedrock converse failed: {exc}") from exc

        blocks = (response.get("output") or {}).get("message", {}).get("content") or []
        return "".join(b.get("text", "") for b in blocks)


class OllamaModel:
    """A local model over Ollama's HTTP API, for the Build It track.

    Raises `ProviderUnavailable` when Ollama is not running. It never fabricates
    a structured address: an ablation row backed by a stub would be fiction, and
    a missing row is honest.
    """

    def __init__(
        self, model_id: str = "llama3.2", endpoint: str = "http://127.0.0.1:11434"
    ) -> None:
        self.model_id = model_id
        self.name = f"ollama:{model_id}"
        self.endpoint = endpoint.rstrip("/")

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        payload = json.dumps(
            {
                "model": self.model_id,
                "system": system,
                "prompt": user,
                "stream": False,
                # Ollama honours a JSON output mode, which removes most of the
                # code-fence wrapping that `extract_json` otherwise has to undo.
                "format": "json",
                "options": {"temperature": 0.0, "num_predict": max_tokens},
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            f"{self.endpoint}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(
                f"Ollama is not reachable at {self.endpoint}: {exc}. "
                f"Start it with `ollama serve && ollama pull {self.model_id}`."
            ) from exc
        except ValueError as exc:
            raise ProviderUnavailable(f"Ollama returned non-JSON: {exc}") from exc

        return body.get("response", "")


def structure(
    *,
    raw_text: str,
    deterministic: dict[str, Any],
    candidates: list[Candidate],
    cfg: Config,
    cheap_model: Any,
    strong_model: Any | None = None,
    multi_script: bool = False,
    allow_escalation: bool = True,
) -> StructureResult:
    """Run S3, with one retry on malformed JSON and one optional escalation."""
    system = load_prompt("structure.txt")
    candidate_ids = {c.landmark_id for c in candidates}
    user = build_user_prompt(
        raw_text=raw_text, deterministic=deterministic, candidates=candidates
    )

    def call(model: Any, prompt: str, retries_so_far: int) -> StructureResult:
        raw = model.complete_json(system, prompt, max_tokens=cfg.models.max_tokens)
        payload = extract_json(raw)
        parsed = normalise_model_output(
            payload, deterministic=deterministic, candidate_ids=candidate_ids
        )
        parsed.model_used = getattr(model, "name", str(model))
        parsed.retries = retries_so_far
        return parsed

    # --- cheap model, with one retry on invalid JSON -----------------------
    try:
        result = call(cheap_model, user, 0)
    except ValueError as exc:
        # Malformed JSON. Exactly one retry, with a stricter reminder.
        try:
            result = call(cheap_model, user + STRICTER_REMINDER, 1)
            result.evidence.append(
                f"first response was not valid JSON ({exc}); one retry succeeded"
            )
        except (ValueError, ProviderUnavailable) as exc2:
            return StructureResult(
                failed=True,
                model_used=getattr(cheap_model, "name", None),
                retries=1,
                escalation_reasons=[EscalationReason.INVALID_JSON.value],
                evidence=[
                    f"S3 failed: model did not return valid JSON after one retry "
                    f"({exc2}); falling back to the deterministic result only"
                ],
            )
    except ProviderUnavailable as exc:
        return StructureResult(
            failed=True,
            unavailable=True,
            evidence=[
                f"S3 unavailable ({exc}); falling back to the deterministic "
                f"result with confidence capped accordingly"
            ],
        )

    result.evidence.append(
        f"structured by {result.model_used}"
        + (f" after {result.retries} retry" if result.retries else "")
    )

    # --- escalate if warranted --------------------------------------------
    reasons = should_escalate(result, multi_script=multi_script)
    if reasons and allow_escalation and strong_model is not None:
        try:
            escalated = call(strong_model, user, result.retries)
            escalated.escalated = True
            escalated.escalation_reasons = reasons
            escalated.evidence = [
                *result.evidence,
                f"escalated to {escalated.model_used} because: {', '.join(reasons)}",
            ]
            return escalated
        except (ValueError, ProviderUnavailable) as exc:
            # Keep the cheap model's answer. It is worse than the strong one
            # might have been, but it is real, and the reason is recorded.
            result.escalation_reasons = reasons
            result.evidence.append(
                f"escalation to the strong model failed ({exc}); "
                f"keeping the cheaper result"
            )
            return result

    if reasons:
        result.escalation_reasons = reasons
        result.evidence.append(
            f"escalation warranted ({', '.join(reasons)}) but not performed"
        )
    return result
