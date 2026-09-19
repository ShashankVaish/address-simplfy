"""Clarifier: template fallback always works; agent output is validated."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions" / "clarifier"))

from agent import ClarifierInput, Question, generate

INP = ClarifierInput(
    field="building",
    known={"locality": "Ramesh Nagar", "pincode": "110015"},
    script="devanagari",
    fallback_question="Flat ya makaan number kya hai?",
    fallback_language="hi-IN",
)


class FakeAgent:
    def __init__(self, output):
        self.output = output

    def structured_output(self, model, prompt):
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class TestGenerate:
    def test_uses_agent_output_when_valid(self) -> None:
        q, source = generate(
            INP,
            agent=FakeAgent(
                Question(question="रमेश नगर मिल गया। फ्लैट नंबर क्या है?", language="hi-IN")
            ),
        )
        assert source == "agent" and q.question.startswith("रमेश")

    def test_falls_back_when_agent_raises(self) -> None:
        q, source = generate(INP, agent=FakeAgent(RuntimeError("bedrock down")))
        assert source == "template" and q.question == INP.fallback_question

    def test_falls_back_on_two_questions(self) -> None:
        _q, source = generate(
            INP, agent=FakeAgent(Question(question="Flat? Floor?", language="hi-IN"))
        )
        assert source == "template"

    def test_falls_back_when_no_sdk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real = builtins.__import__

        def no_strands(name, *a, **k):
            if name.startswith("strands"):
                raise ImportError(name)
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_strands)
        q, source = generate(INP)
        assert source == "template" and q.language == "hi-IN"

    def test_schema_rejects_bad_language(self) -> None:
        with pytest.raises(ValueError):
            Question(question="hello there", language="fr-FR")
