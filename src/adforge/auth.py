"""Single-user password hashing and signed browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from getpass import getpass
from typing import Any

PBKDF2_ITERATIONS = 600_000


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    password_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), password_salt, PBKDF2_ITERATIONS
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(PBKDF2_ITERATIONS),
            base64.urlsafe_b64encode(password_salt).decode(),
            base64.urlsafe_b64encode(digest).decode(),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            base64.urlsafe_b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(base64.urlsafe_b64encode(digest).decode(), expected)
    except (ValueError, TypeError):
        return False


class SessionSigner:
    def __init__(self, secret_key: str, *, max_age_seconds: int = 43_200) -> None:
        if len(secret_key) < 32 or secret_key == "CHANGE_ME":  # noqa: S105
            raise ValueError("ADFORGE_SECRET_KEY must be a non-default value of 32+ characters")
        self.key = secret_key.encode()
        self.max_age_seconds = max_age_seconds

    def create(self) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(24)
        payload = {"sub": "owner", "iat": int(time.time()), "csrf": csrf}
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        signature = hmac.new(self.key, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}", csrf

    def verify(self, token: str | None) -> dict[str, Any] | None:
        if not token or "." not in token:
            return None
        encoded, signature = token.rsplit(".", 1)
        expected = hmac.new(self.key, encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(encoded))
            issued_at = int(payload["iat"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        if issued_at > int(time.time()) + 60 or int(time.time()) - issued_at > self.max_age_seconds:
            return None
        return payload if payload.get("sub") == "owner" else None


def main() -> int:
    password = getpass("New AdForge owner password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("passwords do not match")
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
