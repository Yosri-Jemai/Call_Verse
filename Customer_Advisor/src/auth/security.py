from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from src.config import settings

password_hasher = PasswordHasher()
ROLES = frozenset({"approver", "viewer"})
_DUMMY_PASSWORD_HASH = password_hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def _jwt_secret() -> str:
    secret = settings.jwt_secret
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 bytes.")
    return secret


def validate_jwt_configuration() -> None:
    _jwt_secret()


def create_access_token(user_id: int, role: str) -> tuple[str, datetime]:
    if role not in ROLES:
        raise ValueError("Unsupported user role.")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = now + timedelta(minutes=settings.jwt_expire_minutes)
    claims = {
        "sub": str(user_id),
        "role": role,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid4()),
    }
    token = jwt.encode(claims, _jwt_secret(), algorithm="HS256")
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    claims = jwt.decode(
        token,
        _jwt_secret(),
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "role", "iss", "aud", "iat", "exp"]},
    )
    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("Invalid subject.") from exc
    if user_id < 1 or claims["role"] not in ROLES:
        raise jwt.InvalidTokenError("Invalid user claims.")
    return claims


def dummy_password_verify(password: str) -> None:
    verify_password(password, _DUMMY_PASSWORD_HASH)
