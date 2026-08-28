"""Worker bearer-token authentication, fully separate from owner session auth."""

from __future__ import annotations

import secrets

from adforge.auth import hash_password, verify_password
from adforge.models import WorkerNode, WorkerToken, utc_now
from adforge.services import Services

TOKEN_BYTES = 32


class WorkerAuthError(RuntimeError):
    pass


def issue_token(services: Services, worker: WorkerNode, *, label: str = "default") -> str:
    """Create and persist a new hashed token for a worker, returning the raw value once."""
    secret = secrets.token_urlsafe(TOKEN_BYTES)
    raw_token = f"{worker.id}.{secret}"
    for existing in services.worker_tokens.find_by("worker_id", worker.id):
        if not existing.revoked:
            services.worker_tokens.save(existing.model_copy(update={"revoked": True}))
    services.worker_tokens.save(
        WorkerToken(worker_id=worker.id, token_hash=hash_password(secret), label=label)
    )
    return raw_token


def authenticate(services: Services, raw_token: str | None) -> WorkerNode:
    if not raw_token or "." not in raw_token:
        raise WorkerAuthError("missing or malformed worker token")
    worker_id, secret = raw_token.split(".", 1)
    tokens = [
        token
        for token in services.worker_tokens.find_by("worker_id", worker_id)
        if not token.revoked
    ]
    match = next((token for token in tokens if verify_password(secret, token.token_hash)), None)
    if match is None:
        raise WorkerAuthError("worker token is invalid or revoked")
    worker = services.worker_nodes.get(worker_id)
    if worker is None:
        raise WorkerAuthError("worker no longer exists")
    services.worker_tokens.save(match.model_copy(update={"last_used_at": utc_now()}))
    return worker
