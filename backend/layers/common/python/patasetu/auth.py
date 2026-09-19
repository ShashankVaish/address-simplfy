"""Cognito JWT verification for the operator routes (NFR-16).

The playground is public by design; the review queue, feedback, order
timelines and the authorizer are not. When `PROTECT_OPERATOR_ROUTES=true`,
those handlers call `require_operator(event)` and refuse anything without a
valid `Authorization: Bearer <id-token>` from the stack's Cognito user pool.

Verification is done in the Lambda rather than by an API Gateway JWT
authorizer for one practical reason: the switch is a CloudFormation
*parameter*, and SAM cannot attach a route authorizer conditionally -- the
value has to be literal at transform time. An environment variable can be
`!Ref`'d, so the same deploy can flip protection on and off.

What is checked, in order: the token parses; the signature verifies against
the pool's JWKS (RS256, fetched once per container and cached); `iss` is the
pool; `aud` (or `client_id` for access tokens) is the app client; the token
has not expired. Anything else is a 401 with a short reason and no detail
that would help an attacker.
"""

from __future__ import annotations

import functools
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any


class Unauthorized(Exception):
    """Raised with a short, safe reason. Handlers turn it into a 401."""


@dataclass(frozen=True)
class Operator:
    subject: str
    email: str | None
    username: str | None


def protection_enabled() -> bool:
    return os.environ.get("PROTECT_OPERATOR_ROUTES", "false").strip().lower() == "true"


@functools.lru_cache(maxsize=4)
def _jwks(issuer: str) -> dict[str, Any]:
    """The pool's public keys, fetched once per container."""
    url = f"{issuer}/.well-known/jwks.json"
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _bearer(event: dict[str, Any]) -> str:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    value = headers.get("authorization", "")
    if not value.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    token = value[7:].strip()
    if not token:
        raise Unauthorized("missing bearer token")
    return token


def verify(token: str, *, issuer: str, audience: str) -> Operator:
    """Verify a Cognito id or access token. Raises Unauthorized."""
    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError as exc:  # pragma: no cover - installed in the layer
        raise Unauthorized("token verification unavailable") from exc

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise Unauthorized("malformed token") from exc
    if header.get("alg") != "RS256":
        raise Unauthorized("unexpected signing algorithm")

    try:
        key = PyJWKClient(
            f"{issuer}/.well-known/jwks.json", cache_keys=True
        ).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=issuer,
            # Cognito id tokens carry `aud`; access tokens carry `client_id`
            # instead and no `aud`, so audience is checked below by hand.
            options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("token expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("invalid token") from exc

    if claims.get("aud", claims.get("client_id")) != audience:
        raise Unauthorized("token not issued for this client")
    if claims.get("token_use") not in (None, "id", "access"):
        raise Unauthorized("unexpected token use")

    return Operator(
        subject=str(claims["sub"]),
        email=claims.get("email"),
        username=claims.get("cognito:username") or claims.get("username"),
    )


def require_operator(event: dict[str, Any]) -> Operator | None:
    """Enforce protection if enabled. Returns the operator, or None if off."""
    if not protection_enabled():
        return None
    issuer = os.environ.get("COGNITO_ISSUER", "")
    audience = os.environ.get("COGNITO_AUDIENCE", "")
    if not issuer or not audience:
        # Misconfiguration must fail closed: a protected route with no pool to
        # verify against admits nobody, not everybody.
        raise Unauthorized("operator authentication is not configured")
    return verify(_bearer(event), issuer=issuer, audience=audience)
