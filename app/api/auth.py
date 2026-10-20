"""Identity resolution for a service that sits behind an authentication edge.

Two integration modes are supported, because microservice ecosystems differ:

* `gateway` — an API gateway terminates credentials and forwards the verified
  identity as headers. The service only honours those headers when the TCP peer
  is inside `CLINIC_TRUSTED_PROXY_CIDRS`; a direct caller cannot forge them.
* `jwt` — the caller presents a bearer token signed with a shared HS256 secret.
  No gateway is required, so this works from any trusted client.

`none` exists for local development and tests only, and is refused in production.
"""
import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import cast

from fastapi import HTTPException, Request, status

from app.core.config import settings

KNOWN_ROLES = frozenset({"admin", "doctor", "patient"})


@dataclass(frozen=True)
class Principal:
    actor_id: str
    role: str


class AuthError(HTTPException):
    def __init__(self, detail: str, *, bearer: bool = False) -> None:
        headers = {"WWW-Authenticate": "Bearer"} if bearer else None
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers=headers
        )


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise AuthError("Malformed credentials") from exc


def _decode_jwt(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("Malformed bearer token", bearer=True)
    header_segment, payload_segment, signature_segment = parts
    if not settings.jwt_secret:
        raise AuthError("JWT authentication is not configured", bearer=True)

    expected = hmac.new(
        settings.jwt_secret.encode(),
        f"{header_segment}.{payload_segment}".encode(),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected, _b64url_decode(signature_segment)):
        raise AuthError("Invalid token signature", bearer=True)

    header = cast(dict[str, object], json.loads(_b64url_decode(header_segment)))
    if header.get("alg") != "HS256":
        # Never accept "none" or an unexpected algorithm.
        raise AuthError("Unsupported token algorithm", bearer=True)

    claims = cast(dict[str, object], json.loads(_b64url_decode(payload_segment)))
    now = int(time.time())
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and int(exp) < now:
        raise AuthError("Token expired", bearer=True)
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and int(nbf) > now:
        raise AuthError("Token not yet valid", bearer=True)
    if settings.jwt_issuer and claims.get("iss") != settings.jwt_issuer:
        raise AuthError("Unexpected token issuer", bearer=True)
    if settings.jwt_audience:
        aud = claims.get("aud")
        audiences = aud if isinstance(aud, list) else [aud]
        if settings.jwt_audience not in audiences:
            raise AuthError("Unexpected token audience", bearer=True)
    return claims


def _role_from_claims(claims: dict[str, object]) -> str:
    role = claims.get("role")
    if role is None:
        roles = claims.get("roles")
        if isinstance(roles, list) and roles:
            role = roles[0]
    if not isinstance(role, str) or role not in KNOWN_ROLES:
        raise AuthError("Token carries no usable role", bearer=True)
    return role


def _peer_is_trusted(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    try:
        peer = ip_address(client.host)
    except ValueError:
        return False
    for entry in settings.trusted_proxy_cidrs.split(","):
        cidr = entry.strip()
        if not cidr:
            continue
        try:
            if peer in ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _principal_from_gateway(request: Request) -> Principal:
    if not _peer_is_trusted(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caller is not a trusted gateway",
        )
    actor_id = request.headers.get("X-Actor-Id")
    role = request.headers.get("X-Actor-Role")
    if not actor_id or not role:
        raise AuthError("Identity required")
    if role not in KNOWN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unknown role"
        )
    return Principal(actor_id=actor_id, role=role)


def _principal_from_bearer(request: Request) -> Principal:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("Bearer token required", bearer=True)
    claims = _decode_jwt(token)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthError("Token carries no subject", bearer=True)
    return Principal(actor_id=subject, role=_role_from_claims(claims))


async def get_principal(request: Request) -> Principal:
    mode = settings.auth_mode
    if mode == "none":
        return Principal(actor_id="anonymous", role="admin")
    if mode == "gateway":
        return _principal_from_gateway(request)
    return _principal_from_bearer(request)
