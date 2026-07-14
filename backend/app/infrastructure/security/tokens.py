import hashlib
import secrets
from datetime import timedelta
from typing import Any

import jwt
from app.config import Settings
from app.domain.clock import Clock
from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(admin_id: str, settings: Settings, clock: Clock) -> str:
    now = clock.now()
    payload: dict[str, Any] = {
        "sub": admin_id,
        "type": "admin_access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def decode_access_token(token: str, settings: Settings) -> str:
    payload = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"])
    if payload.get("type") != "admin_access" or not isinstance(payload.get("sub"), str):
        raise jwt.InvalidTokenError("invalid token type")
    return str(payload["sub"])


def create_refresh_token() -> str:
    return f"nfr_{secrets.token_urlsafe(48)}"


def create_api_key() -> str:
    return f"nfy_{secrets.token_urlsafe(32)}"


import hmac

def generate_media_signature(asset_id: str, expires: int, key: str) -> str:
    message = f"{asset_id}:{expires}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_media_signature(asset_id: str, expires: int, sig: str, key: str) -> bool:
    expected = generate_media_signature(asset_id, expires, key)
    return hmac.compare_digest(expected, sig)

