"""Cedar authorisation for outreach and for auto-overwriting addresses.

Every outbound customer contact passes through `authorize_contact` before it
is sent (FR-21). The decision is made by the real Cedar engine (`cedarpy`, the
Python binding of AWS's open-source policy language) evaluating the policy
files in `policies/`. Nothing here decides anything itself; this module only
builds the request, runs the evaluator, and turns the result into something a
review queue can display.

Three properties are load-bearing:

*   **Fail closed.** Any error -- a malformed policy, a missing attribute, the
    evaluator itself raising -- is a DENY with the error as the reason. An
    automated system that texts customers must never send because a check
    crashed.
*   **A denial is never silent.** It carries the matched policy id and a
    human-readable reason, and the caller routes it to the review queue (FR-23).
*   **The policy id in the response is the id in the file.** `@id("...")`
    annotations in the `.cedar` files are what `reasons` returns, so an
    operator reading "denied by contact-opted-out" can open the file and find
    that exact rule.
"""

from __future__ import annotations

import functools
import json
import os
from dataclasses import dataclass, field
from typing import Any

_POLICY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policies")


# Confidence is passed to Cedar as an integer percentage because the language
# has no float literals. The thresholds in the policy files are written in the
# same unit, so this is the single place the conversion happens.
def to_pct(confidence: float) -> int:
    return round(max(0.0, min(1.0, confidence)) * 100)


class AuthzUnavailable(RuntimeError):
    """The evaluator cannot run. Callers must treat this as DENY."""


@functools.lru_cache(maxsize=1)
def load_policies() -> str:
    """Concatenate every .cedar file in the policy directory, once."""
    parts: list[str] = []
    for name in sorted(os.listdir(_POLICY_DIR)):
        if name.endswith(".cedar"):
            with open(os.path.join(_POLICY_DIR, name), encoding="utf-8") as fh:
                parts.append(fh.read())
    if not parts:
        raise AuthzUnavailable(f"no .cedar policies found in {_POLICY_DIR}")
    return "\n".join(parts)


@functools.lru_cache(maxsize=1)
def policy_ids() -> list[str]:
    """The @id annotation of each policy, in file order.

    The evaluator names policies positionally ("policy0", "policy1", ...) in
    the order they appear in the concatenated text. The annotations are the
    names humans use, so a positional id is translated through this list. The
    file order is deterministic (sorted filenames, top to bottom), which is
    what makes the translation safe.
    """
    ids: list[str] = []
    pending: str | None = None
    for line in load_policies().splitlines():
        stripped = line.strip()
        if stripped.startswith("@id("):
            pending = stripped[len('@id("') : stripped.index('")')]
        elif stripped.startswith(("permit(", "forbid(")):
            ids.append(pending or f"policy{len(ids)}")
            pending = None
    return ids


def _name(policy_ref: str) -> str:
    """'policy1' -> its @id annotation; anything else passes through."""
    if policy_ref.startswith("policy") and policy_ref[6:].isdigit():
        index = int(policy_ref[6:])
        ids = policy_ids()
        if index < len(ids):
            return ids[index]
    return policy_ref


@functools.lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    path = os.path.join(_POLICY_DIR, "schema.cedarschema.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class Decision:
    """What the caller gets back. Enough to act on and enough to explain."""

    allowed: bool
    action: str
    principal: str
    resource: str
    # Ids of the policies that determined the outcome, from @id annotations.
    matched_policies: list[str] = field(default_factory=list)
    # One line an operator can read in the queue.
    reason: str = ""
    # Evaluator errors, if any. Non-empty always means DENY.
    errors: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def decision(self) -> str:
        return "ALLOW" if self.allowed else "DENY"

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "allowed": self.allowed,
            "action": self.action,
            "principal": self.principal,
            "resource": self.resource,
            "matched_policies": self.matched_policies,
            "reason": self.reason,
            "errors": self.errors,
            "context": self.context,
        }


def _order_entity(
    order_id: str, *, pending: bool, opted_out: bool
) -> list[dict[str, Any]]:
    """The entity graph for one request.

    Group membership is how the policies see state: an order that is still
    unresolved is `in OrderGroup::"pending_resolution"`, and a customer who has
    opted out puts the order `in OrderGroup::"opted_out"`. Both can be true at
    once, which is exactly the case the forbid rule exists for.
    """
    parents = []
    if pending:
        parents.append({"type": "OrderGroup", "id": "pending_resolution"})
    if opted_out:
        parents.append({"type": "OrderGroup", "id": "opted_out"})
    return [
        {"uid": {"type": "Agent", "id": "clarifier"}, "attrs": {}, "parents": []},
        {"uid": {"type": "Agent", "id": "operator"}, "attrs": {}, "parents": []},
        {"uid": {"type": "Agent", "id": "resolver"}, "attrs": {}, "parents": []},
        {
            "uid": {"type": "OrderGroup", "id": "pending_resolution"},
            "attrs": {},
            "parents": [],
        },
        {"uid": {"type": "OrderGroup", "id": "opted_out"}, "attrs": {}, "parents": []},
        {"uid": {"type": "Order", "id": order_id}, "attrs": {}, "parents": parents},
    ]


def _explain_contact_denial(
    ctx: dict[str, Any], *, opted_out: bool, pending: bool
) -> str:
    """A human sentence for why no permit matched.

    Cedar reports *which* policy decided, not *why a permit's condition was
    false*. For the queue we want the sentence, so the conditions of the
    contact rule are re-stated here in plain words. This explanation is
    advisory; the decision itself always comes from the evaluator.
    """
    if opted_out:
        return "customer has opted out of contact"
    if not pending:
        return "order is not pending resolution"
    hour = ctx.get("local_hour")
    if hour is not None and not (9 <= hour <= 20):
        return f"outside contact hours (local time {hour:02d}:00; allowed 09:00-20:00)"
    if ctx.get("messages_sent_for_order", 0) > 0:
        return "a message has already been sent for this order"
    if ctx.get("confidence_pct", 0) >= 80:
        return "confidence is already above the clarification threshold"
    if ctx.get("channel") != "sms":
        return f"channel {ctx.get('channel')!r} is not an approved contact channel"
    return "no permit policy matched"


def _evaluate(
    request: dict[str, Any], entities: list[dict[str, Any]]
) -> tuple[bool, list[str], list[str]]:
    """Run the real evaluator. Raises AuthzUnavailable if it cannot."""
    try:
        import cedarpy
    except ImportError as exc:
        raise AuthzUnavailable("cedarpy is not installed") from exc
    try:
        result = cedarpy.is_authorized(
            request, load_policies(), entities, load_schema()
        )
    except Exception as exc:
        raise AuthzUnavailable(f"Cedar evaluation failed: {exc}") from exc

    allowed = result.decision == cedarpy.Decision.Allow
    diagnostics = getattr(result, "diagnostics", None)
    reasons = [_name(r) for r in (getattr(diagnostics, "reasons", []) or [])]
    errors = [str(e) for e in (getattr(diagnostics, "errors", []) or [])]
    return allowed, reasons, errors


def authorize_contact(
    *,
    order_id: str,
    confidence: float,
    messages_sent_for_order: int,
    local_hour: int,
    channel: str = "sms",
    opted_out: bool = False,
    pending: bool = True,
    principal: str = "clarifier",
) -> Decision:
    """May `principal` contact the customer for `order_id` right now?

    Fails closed: every failure path returns a DENY with the reason attached.
    """
    ctx = {
        "confidence_pct": to_pct(confidence),
        "messages_sent_for_order": int(messages_sent_for_order),
        "local_hour": int(local_hour),
        "channel": channel,
    }
    request = {
        "principal": f'Agent::"{principal}"',
        "action": 'Action::"contactCustomer"',
        "resource": f'Order::"{order_id}"',
        "context": ctx,
    }
    base = Decision(
        allowed=False,
        action="contactCustomer",
        principal=principal,
        resource=order_id,
        context=ctx,
    )
    try:
        allowed, reasons, errors = _evaluate(
            request, _order_entity(order_id, pending=pending, opted_out=opted_out)
        )
    except AuthzUnavailable as exc:
        base.errors = [str(exc)]
        base.reason = f"authorisation unavailable; failing closed: {exc}"
        return base

    base.matched_policies = reasons
    base.errors = errors
    if errors:
        # An evaluation error (e.g. a missing attribute) is a DENY regardless
        # of what the decision field says.
        base.allowed = False
        base.reason = "policy evaluation reported errors; failing closed"
        return base

    base.allowed = allowed
    if allowed:
        base.reason = f"permitted by {', '.join(reasons) or 'policy'}"
    elif reasons:
        base.reason = f"forbidden by {', '.join(reasons)}: " + _explain_contact_denial(
            ctx, opted_out=opted_out, pending=pending
        )
    else:
        base.reason = "no permit matched: " + _explain_contact_denial(
            ctx, opted_out=opted_out, pending=pending
        )
    return base


def authorize_overwrite(
    *, order_id: str, confidence: float, geo_source: str
) -> Decision:
    """May the resolver overwrite the stored address automatically? (FR-24)"""
    ctx = {"confidence_pct": to_pct(confidence), "geo_source": geo_source}
    request = {
        "principal": 'Agent::"resolver"',
        "action": 'Action::"overwriteStoredAddress"',
        "resource": f'Order::"{order_id}"',
        "context": ctx,
    }
    base = Decision(
        allowed=False,
        action="overwriteStoredAddress",
        principal="resolver",
        resource=order_id,
        context=ctx,
    )
    try:
        allowed, reasons, errors = _evaluate(
            request, _order_entity(order_id, pending=False, opted_out=False)
        )
    except AuthzUnavailable as exc:
        base.errors = [str(exc)]
        base.reason = f"authorisation unavailable; failing closed: {exc}"
        return base

    base.matched_policies = reasons
    base.errors = errors
    base.allowed = allowed and not errors
    if base.allowed:
        base.reason = f"permitted by {', '.join(reasons)}"
    elif ctx["confidence_pct"] <= 95:
        base.reason = f"confidence {ctx['confidence_pct']}% is not above 95%"
    elif geo_source != "landmark_graph":
        base.reason = f"geo source {geo_source!r} is not the landmark graph"
    else:
        base.reason = "no permit matched"
    return base
