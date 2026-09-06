from __future__ import annotations

from base64 import b64decode, b64encode
from datetime import UTC, datetime, timedelta
from hashlib import scrypt
from hmac import compare_digest
from secrets import token_bytes

import jwt


def hash_password(password: str) -> str:
    salt = token_bytes(16)
    derived_key = scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${b64encode(salt).decode()}${b64encode(derived_key).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, n, r, p, encoded_salt, encoded_key = stored_hash.split("$")
        if algorithm != "scrypt":
            return False
        derived_key = scrypt(
            password.encode(),
            salt=b64decode(encoded_salt),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return compare_digest(derived_key, b64decode(encoded_key))
    except (TypeError, ValueError):
        return False


def create_access_token(subject: str, role: str, secret: str, expiration_minutes: int) -> str:
    if len(secret.encode()) < 32:
        raise ValueError("JWT secret must be at least 32 bytes")
    expires_at = datetime.now(UTC) + timedelta(minutes=expiration_minutes)
    return jwt.encode({"sub": subject, "role": role, "exp": expires_at}, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict[str, str]:
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    subject = payload.get("sub")
    role = payload.get("role")
    if not isinstance(subject, str) or not isinstance(role, str):
        raise jwt.InvalidTokenError("Missing token claims")
    return {"sub": subject, "role": role}