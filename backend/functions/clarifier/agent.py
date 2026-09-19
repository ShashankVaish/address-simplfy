"""The clarification agent: one question, in the customer's own script.

Built on the Strands Agents SDK (AWS open source), with Bedrock as the model
provider. The agent's job is deliberately narrow -- it does not decide *whether*
to ask (S7 did that) or *what field* to ask about (`decide.choose_question`
did that from the confidence gain). It only writes the question well: short,
polite, in the right language, naming what we already know so the customer
does not repeat it.

Why an agent at all, when a template already produces a serviceable question?
Because the template cannot say "Sunrise Apartments mil gaya" -- it does not
know how to weave the resolved fields into a natural sentence, and reply rates
depend on exactly that. The template stays as the fallback: if the model is
unavailable or returns something unusable, the customer still gets a question.

Structured output is enforced with a Pydantic model, so a chatty model cannot
turn "one question" into three.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

# The Strands import is deferred so the deterministic fallback works in any
# environment -- including the unit tests -- without the SDK installed.

SYSTEM_PROMPT = """You write ONE short clarification question to a delivery customer in India.

You will be given the fields already resolved from their address, the single
field that is missing or uncertain, and the customer's script (Devanagari or
Latin). Reply in the customer's language: Hindi written in Devanagari if they
wrote Devanagari; otherwise natural Hinglish (Hindi in Latin letters, the way
people text) unless the address was entirely English, in which case English.

Rules:
- Exactly one question. Never a list, never two questions joined by "and".
- Under 120 characters. It is an SMS.
- Mention at most one thing we already know, so they trust us ("Ramesh Nagar
  mil gaya") -- never repeat the whole address back.
- Do not ask for anything we already have.
- No greetings, no signature, no emojis, no marketing.
- Never include a phone number, order id, or any personal detail.
- If the missing field is "geo_confirm", ask them to tap their building on the
  map link we will send; do not ask for text.
"""


class Question(BaseModel):
    """What the agent must return. One question, one language tag."""

    question: str = Field(min_length=5, max_length=160)
    language: str = Field(pattern=r"^(hi-IN|en-IN|hi-Latn)$")


@dataclass
class ClarifierInput:
    field: str
    known: dict[str, str]
    script: str  # "devanagari" | "latin"
    fallback_question: str
    fallback_language: str


def _prompt(inp: ClarifierInput) -> str:
    known = ", ".join(f"{k}={v}" for k, v in inp.known.items() if v) or "nothing yet"
    return (
        f"Script the customer used: {inp.script}\n"
        f"Fields already resolved: {known}\n"
        f"Missing or uncertain field to ask about: {inp.field}\n"
        f"Write the question."
    )


logger = logging.getLogger(__name__)


def build_agent(model_id: str | None = None, region: str | None = None) -> Any:
    """A Strands agent over Bedrock. Raises ImportError if Strands is absent."""
    from strands import Agent
    from strands.models.bedrock import BedrockModel

    model = BedrockModel(
        model_id=model_id or os.environ.get("MODEL_CHEAP", "amazon.nova-lite-v1:0"),
        region_name=region or os.environ.get("AWS_REGION", "ap-south-1"),
        temperature=0.2,
        max_tokens=200,
    )
    return Agent(model=model, system_prompt=SYSTEM_PROMPT, callback_handler=None)


def generate(inp: ClarifierInput, *, agent: Any | None = None) -> tuple[Question, str]:
    """Return (question, source) where source is "agent" or "template".

    Every failure path -- SDK missing, model unreachable, output failing the
    schema -- returns the template question. The customer always gets asked;
    only the phrasing quality varies.
    """
    fallback = Question(question=inp.fallback_question, language=inp.fallback_language)
    try:
        agent = agent or build_agent()
    except ImportError as exc:
        logger.warning(
            '{"event": "clarifier_fallback", "reason": "sdk missing: %s"}', exc
        )
        return fallback, "template"

    try:
        result = agent.structured_output(Question, _prompt(inp))
    except Exception as exc:
        # The reason is logged, never the address: the prompt carries fields.
        logger.warning(
            '{"event": "clarifier_fallback", "reason": "%s: %s"}',
            type(exc).__name__,
            str(exc)[:300].replace('"', "'"),
        )
        return fallback, "template"

    if not isinstance(result, Question):
        logger.warning('{"event": "clarifier_fallback", "reason": "not a Question"}')
        return fallback, "template"
    text = result.question.strip()
    # Belt and braces on the two rules a model breaks most: one question, no
    # digits that could be a phone number.
    if text.count("?") > 1 or any(ch.isdigit() for ch in text if text.count(ch) > 6):
        logger.warning(
            '{"event": "clarifier_fallback", "reason": "output failed rules"}'
        )
        return fallback, "template"
    return Question(question=text, language=result.language), "agent"
