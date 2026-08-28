# Distributed Worker Protocol

Phase 16. Implements the register/heartbeat/claim/lease/artifact/complete contract
external workers use to run capabilities the VM does not run itself (Android
capture, Flow browser generation). The synthetic round trip is proven with the
`synthetic_echo` capability; see `tests/test_worker.py` and BLOCKERS.md B-004–B-006
for what still requires a real second machine.

## Principles

- The VM is the control plane. Workers connect outbound over HTTPS only; AdForge
  never opens an inbound connection to a worker, and ADB is never exposed remotely.
- Worker auth is a bearer token, fully separate from the owner's session cookie.
- A worker offline or lacking a capability degrades that capability only. It never
  makes the platform verdict `NOT_READY` on its own (`src/adforge/health.py`).
- A campaign that needs an unavailable capability transitions to
  `WAITING_FOR_WORKER` and resumes automatically when a job completes — it does not
  fail outright.

## Endpoints (`src/adforge/worker_api.py`, mounted at `/api/worker`)

| Endpoint | Purpose |
|---|---|
| `POST /heartbeat` | Report liveness, `agent_version`/`os`/`architecture`, capabilities, and safe metadata. Also doubles as first-contact registration — a worker's `WorkerNode` row is created by the owner from Settings → Workers before the agent ever calls this. |
| `POST /jobs/claim` | Atomically claim one `PENDING` job whose capability the worker reports (`BEGIN IMMEDIATE`, same pattern as `Orchestrator.acquire_lease`). Returns `{"job": null}` when nothing matches. |
| `POST /jobs/{id}/lease` | Renew the lease; rejected (409) if the caller doesn't hold it. |
| `POST /jobs/{id}/progress` | Optional free-text status; does not renew the lease. |
| `POST /jobs/{id}/artifacts` | Multipart upload; server recomputes the sha256 and rejects a mismatch; stored under the campaign workspace. |
| `POST /jobs/{id}/complete` | Idempotent — repeating on an already-`COMPLETE` job is a no-op. Resumes a `WAITING_FOR_WORKER` campaign. |
| `POST /jobs/{id}/fail` | Classifies the error `RETRYABLE`/`NON_RETRYABLE`/`EXTERNAL_ACTION_REQUIRED`. Retryable failures requeue up to 3 total attempts; beyond that (or non-retryable) the job fails and the campaign transitions to `BLOCKED`. |

## Liveness and recovery

- `WorkerJobService.sweep_offline()` flips a `WorkerNode` from `ONLINE` to
  `OFFLINE` once `last_heartbeat_at` exceeds the configured threshold (default 90s).
- `WorkerJobService.reclaim_expired()` returns any `CLAIMED`/`RUNNING` job whose
  lease has expired back to `PENDING` (or `FAILED` once its attempt budget is
  exhausted). It runs on every app start (service-restart recovery) and on every
  claim attempt.
- Job/worker/attempt/artifact records are ordinary SQLite-backed repositories
  (`worker_nodes`, `worker_tokens`, `worker_jobs`, `worker_job_attempts`,
  `worker_artifacts` — migration version 2 in `src/adforge/database.py`), so a
  service restart never loses in-flight state.

## Worker agent (`scripts/worker_agent.py`)

Stdlib + `httpx`, cross-platform. `configure` stores the bootstrap token (file mode
0600); `doctor` reports detected capabilities without touching anything; `start`
runs heartbeat/claim/execute/upload/complete in a loop. Only `synthetic_echo` has a
real handler in this phase; `android_capture` and `flow_generation` jobs are
classified `EXTERNAL_ACTION_REQUIRED` until a real worker build implements them —
the protocol does not need to change to add those handlers later.

## What's proven vs. what's still external

Proven in this repository, including over real HTTP against a running server (see
`docs/IMPLEMENTATION_STATUS.md` Phase 16 evidence): registration via heartbeat,
capability matching, exclusive lease under concurrent claim, lease renewal and
expiry/reclaim, checksum-validated artifact upload, idempotent completion, 3-attempt
retry/classification, offline-worker heartbeat timeout, cross-worker access
rejection, service-restart persistence, and `WAITING_FOR_WORKER` auto-resume.

Not proven here, and not fakeable: a real external machine actually connecting over
the internet, a real Android emulator/device capture, and a real authenticated Flow
generation, run through a worker. These remain external blockers (BLOCKERS.md
B-004–B-006) exactly like the existing product/emulator/Flow blockers B-001–B-003.
