import base64
import hashlib
import hmac
import json
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_token(secret: str, *, sub: str = "patient-1", role: str | None = "patient",
               expires_in: int = 3600, issuer: str | None = None,
               audience: str | None = None, tamper: bool = False) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    claims: dict[str, object] = {"sub": sub, "exp": int(time.time()) + expires_in}
    if role is not None:
        claims["role"] = role
    if issuer:
        claims["iss"] = issuer
    if audience:
        claims["aud"] = audience
    signing_input = (
        f"{_b64(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    )
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    if tamper:
        signature = bytes([signature[0] ^ 0xFF]) + signature[1:]
    return f"{signing_input}.{_b64(signature)}"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def jwt_mode(monkeypatch):
    from app.api import auth
    monkeypatch.setattr(auth.settings, "auth_mode", "jwt")
    monkeypatch.setattr(auth.settings, "jwt_secret", "test-secret")
    monkeypatch.setattr(auth.settings, "jwt_issuer", "")
    monkeypatch.setattr(auth.settings, "jwt_audience", "")
    return auth


@pytest.fixture()
def gateway_mode(monkeypatch):
    from app.api import auth
    monkeypatch.setattr(auth.settings, "auth_mode", "gateway")
    return auth


async def test_jwt_valid_token_can_book(client, seeded, jwt_mode):
    token = make_token("test-secret", sub=str(seeded["patient"].id))
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(10, 10).isoformat()}
    r = await client.post("/appointments", json=body, headers=_auth(token))
    assert r.status_code == 201, r.text


async def test_jwt_missing_header_401(client, jwt_mode):
    r = await client.get(f"/appointments/{uuid.uuid4()}")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


async def test_jwt_expired_401(client, jwt_mode):
    token = make_token("test-secret", expires_in=-60)
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 401


async def test_jwt_bad_secret_401(client, jwt_mode):
    token = make_token("not-the-secret")
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 401


async def test_jwt_tampered_signature_401(client, jwt_mode):
    token = make_token("test-secret", tamper=True)
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 401


async def test_jwt_unknown_role_401(client, jwt_mode):
    token = make_token("test-secret", role="root")
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 401


async def test_jwt_missing_role_401(client, jwt_mode):
    token = make_token("test-secret", role=None)
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 401


async def test_jwt_audience_is_enforced(client, jwt_mode, monkeypatch):
    monkeypatch.setattr(jwt_mode.settings, "jwt_audience", "clinic-api")
    good = make_token("test-secret", audience="clinic-api")
    bad = make_token("test-secret", audience="another-api")
    assert (await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(good))).status_code == 404
    assert (await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(bad))).status_code == 401


async def test_jwt_roles_claim_is_accepted(client, seeded, jwt_mode):
    token = make_token("test-secret", sub=str(seeded["doctor"].id), role=None)
    # roles[] form: build manually through the same helper by injecting a list claim
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"sub": str(seeded["doctor"].id), "roles": ["doctor"], "exp": int(time.time()) + 600}
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    sig = _b64(hmac.new(b"test-secret", signing_input.encode(), hashlib.sha256).digest())
    token = f"{signing_input}.{sig}"
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 404


async def test_gateway_accepts_trusted_peer(client, gateway_mode):
    headers = {"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"}
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404


async def test_gateway_rejects_untrusted_peer(gateway_mode):
    transport = ASGITransport(app=app, client=("203.0.113.7", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as attacker:
        r = await attacker.get(
            f"/appointments/{uuid.uuid4()}",
            headers={"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"},
        )
    assert r.status_code == 403
    assert "trusted gateway" in r.json()["detail"]


async def test_gateway_rejects_unknown_role(client, gateway_mode):
    r = await client.get(f"/appointments/{uuid.uuid4()}",
                         headers={"X-Actor-Id": "x", "X-Actor-Role": "root"})
    assert r.status_code == 403


async def test_gateway_requires_identity(client, gateway_mode):
    r = await client.get(f"/appointments/{uuid.uuid4()}")
    assert r.status_code == 401


async def test_production_refuses_open_mode(monkeypatch):
    from app import main as main_module
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "auth_mode", "none")
    with pytest.raises(RuntimeError):
        async with main_module.lifespan(main_module.app):
            pass


async def test_jwt_malformed_token_401(client, jwt_mode):
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth("abc"))
    assert r.status_code == 401


async def test_jwt_without_configured_secret_401(client, jwt_mode, monkeypatch):
    monkeypatch.setattr(jwt_mode.settings, "jwt_secret", "")
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(make_token("anything")))
    assert r.status_code == 401


async def test_jwt_alg_none_is_rejected(client, jwt_mode):
    # Correctly signed, but the header claims alg=none: must still fail.
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"sub": "attacker", "role": "admin", "exp": int(time.time()) + 600}
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    signature = _b64(hmac.new(b"test-secret", signing_input.encode(), hashlib.sha256).digest())
    r = await client.get(f"/appointments/{uuid.uuid4()}",
                         headers=_auth(f"{signing_input}.{signature}"))
    assert r.status_code == 401


async def test_jwt_not_yet_valid_401(client, jwt_mode):
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"sub": "p1", "role": "patient", "nbf": int(time.time()) + 600}
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    signature = _b64(hmac.new(b"test-secret", signing_input.encode(), hashlib.sha256).digest())
    r = await client.get(f"/appointments/{uuid.uuid4()}",
                         headers=_auth(f"{signing_input}.{signature}"))
    assert r.status_code == 401


async def test_jwt_issuer_mismatch_401(client, jwt_mode, monkeypatch):
    monkeypatch.setattr(jwt_mode.settings, "jwt_issuer", "https://auth.example.com")
    token = make_token("test-secret", issuer="https://evil.example.com")
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 401


async def test_jwt_missing_subject_401(client, jwt_mode):
    token = make_token("test-secret", sub="")
    r = await client.get(f"/appointments/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 401


def test_b64_decode_rejects_invalid_segment(jwt_mode):
    from app.api import auth
    with pytest.raises(auth.AuthError):
        auth._b64url_decode("A")


async def test_gateway_rejects_non_ip_peer(gateway_mode):
    transport = ASGITransport(app=app, client=("not-an-ip", 1))
    async with AsyncClient(transport=transport, base_url="http://test") as caller:
        r = await caller.get(f"/appointments/{uuid.uuid4()}",
                             headers={"X-Actor-Id": "a", "X-Actor-Role": "admin"})
    assert r.status_code == 403


async def test_gateway_tolerates_bad_cidr_entries(client, gateway_mode, monkeypatch):
    monkeypatch.setattr(gateway_mode.settings, "trusted_proxy_cidrs",
                        ", banana ,127.0.0.1/32")
    r = await client.get(f"/appointments/{uuid.uuid4()}",
                         headers={"X-Actor-Id": "a", "X-Actor-Role": "admin"})
    assert r.status_code == 404
