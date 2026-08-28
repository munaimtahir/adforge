# Security Model

## Principles
- Single-user does not mean unauthenticated.
- Internet exposure is expected; Caddy provides TLS/reverse proxy.
- Provider credentials/session data are secrets.
- Secrets never enter prompts, ledgers, logs, manifests, or Git.
- Persistent browser profiles use restrictive permissions.
- Commands executed by AI-controlled workers must be constrained to intended workspaces where practical.
- Uploaded APKs/assets are treated as untrusted files until validated.
- File paths must be normalized to prevent traversal.
- Generated filenames must be controlled by AdForge.
- Destructive actions require explicit internal safeguards.
- Voice cloning requires authorization/provenance.

## Self-modification
Agents may inspect and propose changes. Material changes to AdForge's production code require explicit permission before implementation.

## Distributed workers
- Workers (external machines such as an Android-capable Windows laptop) authenticate with
  a per-worker bearer token, entirely separate from the owner's browser session — a worker
  token never authenticates a web route and a session cookie never authenticates a worker
  API route (`src/adforge/worker_auth.py`, `src/adforge/worker_api.py`).
- Tokens are `secrets.token_urlsafe(32)`-strength, prefixed with the worker id for lookup,
  hashed server-side with the same PBKDF2 machinery as the owner password, and shown to the
  owner exactly once at issuance (Settings → Workers). Revoking or rotating a token takes
  effect immediately because every call re-verifies against stored state.
- Workers only ever initiate outbound HTTPS requests to AdForge. AdForge never opens an
  inbound connection to a worker, and ADB is never exposed off the machine that runs it.
- A worker may only act on jobs it currently holds the lease for; `WorkerJobService`
  rejects `lease`, `progress`, `artifacts`, `complete`, and `fail` calls from any other
  worker (cross-worker job/artifact access is a 409, not a silent no-op).
- Uploaded artifact filenames are validated with `storage.safe_component` (no traversal),
  written under the owning campaign's workspace only, capped at 500MB, and the server
  recomputes the sha256 checksum itself — a mismatch is rejected and the partial file is
  deleted, so a worker cannot claim success for a file it didn't actually send intact.
- Job completion is idempotent (a repeated `complete` call on an already-`COMPLETE` job is
  a no-op, not a duplicate side effect) and failures are classified
  `RETRYABLE`/`NON_RETRYABLE`/`EXTERNAL_ACTION_REQUIRED` with a permanent
  `WorkerJobAttempt` history, mirroring the existing task-attempt model.
- An offline or unavailable worker degrades only the specific capability it provided
  (`android_capture`, `flow_generation`); it never flips the platform verdict to
  `PLATFORM_NOT_READY` on its own (`src/adforge/health.py`).
