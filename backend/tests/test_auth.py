"""Cognito JWT verification, against a locally generated RSA key pair."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from patasetu import auth

ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_TEST"
AUDIENCE = "client-123"
KID = "kid-1"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()

    def b64(n: int) -> str:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": KID,
                "use": "sig",
                "alg": "RS256",
                "n": b64(numbers.n),
                "e": b64(numbers.e),
            }
        ]
    }
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pem, jwks


@pytest.fixture(autouse=True)
def _protected(monkeypatch: pytest.MonkeyPatch, keypair) -> None:
    _pem, jwks = keypair
    monkeypatch.setenv("PROTECT_OPERATOR_ROUTES", "true")
    monkeypatch.setenv("COGNITO_ISSUER", ISSUER)
    monkeypatch.setenv("COGNITO_AUDIENCE", AUDIENCE)
    # Serve our JWKS instead of fetching Cognito's.
    from jwt import PyJWKClient

    monkeypatch.setattr(PyJWKClient, "fetch_data", lambda self: jwks)


def token(pem: bytes, **claims: Any) -> str:
    payload = {
        "sub": "user-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int(time.time()) + 300,
        "email": "ops@example.com",
        "token_use": "id",
        **claims,
    }
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": KID})


def event(authorization: str | None) -> dict:
    return {"headers": {"Authorization": authorization} if authorization else {}}


class TestRequireOperator:
    def test_off_switch_admits_everyone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROTECT_OPERATOR_ROUTES", "false")
        assert auth.require_operator(event(None)) is None

    def test_valid_id_token(self, keypair) -> None:
        op = auth.require_operator(event(f"Bearer {token(keypair[0])}"))
        assert (
            op is not None and op.subject == "user-1" and op.email == "ops@example.com"
        )

    def test_valid_access_token_uses_client_id(self, keypair) -> None:
        t = token(keypair[0], token_use="access", client_id=AUDIENCE)
        # access tokens carry client_id rather than aud
        payload = jwt.decode(t, options={"verify_signature": False})
        payload.pop("aud")
        t = jwt.encode(payload, keypair[0], algorithm="RS256", headers={"kid": KID})
        assert auth.require_operator(event(f"Bearer {t}")) is not None

    @pytest.mark.parametrize(
        "header", [None, "", "Basic abc", "Bearer", "Bearer not.a.jwt"]
    )
    def test_missing_or_malformed(self, header: str | None) -> None:
        with pytest.raises(auth.Unauthorized):
            auth.require_operator(event(header))

    def test_expired(self, keypair) -> None:
        with pytest.raises(auth.Unauthorized, match="expired"):
            auth.require_operator(
                event(f"Bearer {token(keypair[0], exp=int(time.time()) - 10)}")
            )

    def test_wrong_audience(self, keypair) -> None:
        with pytest.raises(auth.Unauthorized, match="client"):
            auth.require_operator(
                event(f"Bearer {token(keypair[0], aud='someone-else')}")
            )

    def test_wrong_issuer(self, keypair) -> None:
        with pytest.raises(auth.Unauthorized):
            auth.require_operator(
                event(f"Bearer {token(keypair[0], iss='https://evil.example')}")
            )

    def test_wrong_key_is_rejected(self, keypair) -> None:
        other = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        ).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        with pytest.raises(auth.Unauthorized):
            auth.require_operator(event(f"Bearer {token(other)}"))

    def test_hs256_downgrade_is_rejected(self) -> None:
        forged = jwt.encode(
            {"sub": "x", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60},
            "secret",
            algorithm="HS256",
        )
        with pytest.raises(auth.Unauthorized, match="algorithm"):
            auth.require_operator(event(f"Bearer {forged}"))

    def test_misconfiguration_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch, keypair
    ) -> None:
        monkeypatch.setenv("COGNITO_ISSUER", "")
        with pytest.raises(auth.Unauthorized, match="not configured"):
            auth.require_operator(event(f"Bearer {token(keypair[0])}"))


class TestHandlersHonourTheSwitch:
    def test_queue_returns_401_without_token(self) -> None:
        from patasetu.store import InMemoryStore
        from tests.test_queue import event as qevent
        from tests.test_queue import load_queue_app

        app = load_queue_app(InMemoryStore())
        r = app.handler(qevent("/v1/queue"))
        assert (
            r["statusCode"] == 401 and json.loads(r["body"])["error"] == "unauthorized"
        )

    def test_queue_admits_a_valid_token(self, keypair) -> None:
        from patasetu.store import InMemoryStore
        from tests.test_queue import event as qevent
        from tests.test_queue import load_queue_app

        app = load_queue_app(InMemoryStore())
        ev = qevent("/v1/queue")
        ev["headers"] = {"authorization": f"Bearer {token(keypair[0])}"}
        assert app.handler(ev)["statusCode"] == 200

    def test_authorizer_returns_401_without_token(self) -> None:
        from patasetu.store import InMemoryStore
        from tests.test_cedar_policies import load_authorizer

        app = load_authorizer(InMemoryStore())
        r = app.handler(
            {
                "requestContext": {"http": {"method": "POST"}},
                "body": json.dumps({"order_id": "x", "confidence": 0.5}),
            }
        )
        assert r["statusCode"] == 401
