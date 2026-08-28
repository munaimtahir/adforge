# Test and Runtime Evidence

Audit date: 2026-08-29 UTC (originally 2026-08-28; see the 2026-08-29 section below for
Phase 18 additions).

## Final automated gates (2026-08-29)

| Gate | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | PASS — 119 tests |
| `.venv/bin/ruff check .` | PASS |
| `.venv/bin/mypy src/adforge` | PASS — strict |
| `git diff --check` | PASS |
| `python scripts/secret_scan.py` | PASS |
| `caddy validate` (live production Caddyfile) | PASS |
| `systemd-analyze verify deploy/adforge.service` | PASS |

## Final automated gates (2026-08-28, original)

| Gate | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | PASS — 83 tests |
| `.venv/bin/ruff check .` | PASS |
| `.venv/bin/mypy src/adforge` | PASS — strict, 22 source files |
| `git diff --check` | PASS |
| JSON Schema self-validation | PASS — Product Truth and asset manifest schemas |
| YAML configuration parse | PASS — `config/defaults.example.yaml` |
| `python scripts/secret_scan.py` | PASS — tracked files |
| `caddy validate --config deploy/Caddyfile` | PASS |
| `systemd-analyze verify deploy/adforge.service` | PASS |
| Python package dependency check | PASS |
| Frontend build | Not applicable — server-rendered Jinja/CSS, route tests pass |

## Runtime integrations

| Integration | Evidence | Honest status |
|---|---|---|
| Claude Code 2.1.248 | Real adapter returned schema-valid `{"status":"ok"}` in 5.619s | PASS |
| Codex CLI 0.150.1 | Real adapter returned schema-valid `{"status":"ok"}` in 8.125s | PASS |
| FFmpeg/ffprobe 6.1.1 | Real H.264/AAC 9:16 fixture render, dimensions/duration/audio probed | PASS |
| Google Chrome 152 + Playwright | Browser launches and reaches Flow; generation control readiness checked | BLOCKED — login/subscription access |
| ADB 36 / emulator 36.4 | Binaries respond; zero devices and zero configured AVDs | BLOCKED — device profile |
| Caddy 2.11.4 / systemd 255 | Deployment configurations validate | PASS |

## Acceptance attempt

- Campaign: `f708b042-7310-4fb1-952e-f7882d8ad79e`.
- Creation through authenticated web UI: HTTP 303.
- Start through authenticated campaign detail: HTTP 409.
- Durable result: `CREATED`, inactive, Product Truth `UNKNOWN`.
- Screenshot: `docs/10-acceptance/evidence/f708b042-7310-4fb1-952e-f7882d8ad79e/campaign-blocked.png`
  (generated evidence exists locally and is ignored by Git).
- Final Warranty Vault MP4: not produced.

## Evidence boundaries

Mocks/fakes prove internal contracts only. Representative fixture files prove handoff
validation only. Procedural audio proves the legal/local fallback and technical mix,
not production narration quality. The fixture worker completion and real fixture MP4
do not substitute for the blocked Warranty Vault campaign.

## Distributed worker evidence (2026-08-28)

| Check | Evidence | Result |
|---|---|---|
| Local synthetic round trip | `tests/test_worker.py` full protocol test suite (16 tests: lease exclusivity, idempotent completion, checksum-validated artifacts, lease expiry/reclaim, 3-attempt retry classification, offline heartbeat timeout, cross-worker access rejection, restart persistence, lease-gated input download) | PASS |
| Public HTTPS synthetic round trip | Worker `public-https-probe-vm` registered, heartbeated, claimed, leased, uploaded a checksum-verified artifact, and completed job `e90b1c89-4182-4542-900e-a0c03d9cf317` against **`https://adforge.vexel.pk`** (not localhost); `WAITING_FOR_WORKER` campaign `79eccabd-631d-4baa-93ea-cff50764d84d` auto-resumed to `CREATED`/active; production `journalctl` shows the real inbound requests through Caddy; credential revoked immediately after | PASS (protocol proof only — not B-004, no real Windows machine involved) |
| Android SDK/AVD discovery | `tests/test_worker_agent.py` ran `find_android_sdk()`/`list_avds()` (read-only) against this VM's real, device-less Android SDK install; correctly located `adb`/`emulator` and correctly reported zero AVDs (no `AdForge_API_36`) | PASS (discovery only; no AVD created or emulator launched, per locked architecture) |
| Flow login-state detection | `tests/test_worker_agent.py` ran a real headless Playwright session against the live `https://labs.google/fx/tools/flow` from this VM; correctly reported `LOGIN_REQUIRED` | PASS (detection only; no generation attempted) |
| Real Android capture | Not attempted — no Android device/emulator worker is reachable, and running one on this VM is architecturally forbidden | BLOCKED — B-005 |
| Real Flow generation | Not attempted — no authenticated Flow session and no worker to run it on | BLOCKED — B-006 |
| Production Claude health (real invocation) | `claude` is not on the `adforge` service account's PATH at all | NOT_READY — B-007 |
| Production Codex health (real invocation) | `codex` resolves on PATH but returns `401 Unauthorized` connecting to `wss://api.openai.com/v1/responses` — no authenticated session for the `adforge` account | BLOCKED — B-007 |
| Production platform verdict (real checks, forced) | `database READY, storage READY, ffmpeg READY, chromium READY, claude NOT_READY, codex BLOCKED, android_capture TEMPORARILY_UNAVAILABLE, flow_generation BLOCKED` | `PLATFORM_NOT_READY` (driven by claude/codex, not by the absent worker capabilities, which correctly only degrade) |

This is the first time Claude/Codex health was checked with a real invocation *as the
production service account* rather than the development account (`munaim`) or a mere
`shutil.which`/`--version` probe — it is the reason B-007 was only discovered now.

## Phase 18 evidence (2026-08-29): real external worker, real Android capture, B-007 fix

All of the following ran against the live production endpoint `https://adforge.vexel.pk`
and the real, standing `adforge-linux-01` worker (a genuinely separate Linux machine),
not localhost, not a fixture, not a mock.

### Real external worker acceptance (B-004)

- Worker `adforge-linux-01` registered, heartbeated, and went `ONLINE`; credential
  issued administratively (same `issue_token()` code path as Settings → Workers),
  never logged, kept as the standing worker identity (not revoked, unlike the earlier
  same-VM `public-https-probe-vm` proof).
- `synthetic_echo` round trip: job `798b8150-7260-4e0f-8ede-d6c279f72740` — register →
  heartbeat → claim → lease → checksum-verified artifact upload → complete.
- Offline-dispatch/reconnect round trip: campaign
  `7604c7f6-9244-45f0-9c03-34bb8676d469` reached `APP_CAPTURE` and dispatched an
  `android_capture` `WorkerJob` (`22f3c205-9e17-45ac-8b51-482651697b71`) while the
  worker was verifiably `OFFLINE` (no heartbeat within the timeout window); campaign
  correctly entered `WAITING_FOR_WORKER`, not `FAILED`; the worker then reconnected,
  claimed, executed, and completed it, and the campaign auto-resumed and advanced
  (confirmed `BLOCKED` only at the next, unimplemented stage — expected).

### Real Android capture acceptance (B-005, B-002)

- A real, signed, installable APK (`pk.fictional.demotask`, "DemoTask") was authored
  and compiled from source on `adforge-linux-01` using the Android SDK build tools
  already present (`javac`, `d8`, `aapt2`, `zipalign`, `apksigner` with a locally
  generated debug keystore) — not downloaded from any external source. `aapt dump
  badging` and a manual install/launch on the canonical AVD (screenshot attached to
  this session, not committed) both confirmed it before wiring it into the pipeline.
- Campaign `7d26cb2e-8474-4adc-a3b9-1a96f3d0d133`: APK ingested via `APKIngestor`
  through the real `/opt/adforge/imports` import root; driven to `APP_CAPTURE`; the
  real `build_app_capture_handler` (not a test double) created `WorkerJob`
  `079749fc-bc9d-4f37-a1f1-4e3195854ae3` with the correct payload
  (`apk_relative_path`, `apk_filename`, `apk_sha256`, `package_id`).
- `adforge-linux-01` claimed it, downloaded the APK via the lease-gated input
  endpoint, checksum-verified it, booted the canonical AVD, installed, cleared state,
  launched, captured `screenshot.png` + `recording.mp4` (10s `screenrecord`),
  `adb.log` diagnostics, `device.json`, `capture.json`, and `checksums.json`, uploaded
  all six as checksum-verified `WorkerArtifact`s, and completed.
- The artifact importer created real `Asset` records (`app_capture_image`,
  `app_capture_video`) referencing the uploaded files directly; the campaign
  auto-resumed and advanced past `APP_CAPTURE`.
- **Server-side independent validation**: `ffprobe` on the production VM against the
  uploaded `recording.mp4` confirmed a real H.264 stream at **1080×1920**, 3.39s
  duration — not a placeholder file.
- **Resolution bug found and fixed**: before the fix, this same pipeline would have
  produced a 1080×2400 recording while `device.json` falsely claimed 1080×1920
  (`ensure_canonical_avd()` declared the canonical resolution but never applied it to
  the AVD config). Fixed in `scripts/worker_agent.py`
  (`apply_canonical_display_config`); re-verified via a clean boot
  (`wm size` → `1080x1920`) before this acceptance run.

### Lease crash/reclaim/retry (Phase 7 scenarios), live

- **Scenario A** (worker offline at dispatch): see the reconnect round trip above.
- **Scenario B** (worker crashes mid-job): campaign
  `1eca8c04-5d58-4f9c-95c0-7742ffa85759`, job `f9fa6125-0313-4edd-81fc-59d38c984ab9`
  claimed by the real worker node (simulating a claim that never completes), then
  `reclaim_expired()` correctly requeued it to `PENDING` (worker_id cleared, attempt
  count preserved at 1) after the real lease window
  (`DEFAULT_LEASE_SECONDS = 120s`) passed. The worker then completed it for real on
  attempt 2. Verified: exactly one `COMPLETE` status, no duplicate `WorkerArtifact`s
  or `Asset`s, full `WorkerJobAttempt` history preserved
  (`CLAIMED` → `CLAIMED` → `COMPLETE`), campaign advanced exactly once.

### Production bugs found and fixed in passing

- A stale `active=True` lease on an earlier session's probe campaign
  (`79eccabd-631d-4baa-93ea-cff50764d84d`) silently blocked all campaign resumption
  system-wide (only one campaign may hold the active lease). Released via
  `Orchestrator.release_lease()`.
- `APKIngestor` can never resolve `package_id` on the production control plane
  (no Android SDK there by design), yet the `android_capture` `WorkerJob` payload
  requires it — a real design gap, not yet fixed; documented in `docs/BLOCKERS.md`.

### B-007 (2026-08-29)

Re-diagnosed with the correct `HOME`/working directory as the `adforge` account (the
original 401/permission-error readings were confounded by `codex` resolving "project
config" relative to cwd, not `$HOME`). `claude` was fixed onto PATH via
`sudo npm install -g @anthropic-ai/claude-code` (system-wide `/usr/local/bin`, no
copying of personal credentials). Real invocations now cleanly report:

| Check | Result |
|---|---|
| `claude --version` (as `adforge`) | `2.1.250 (Claude Code)` — works |
| `claude -p "..."` (as `adforge`) | `Not logged in · Please run /login` — clean auth-required, not a PATH error |
| `codex exec --skip-git-repo-check "..."` (as `adforge`) | `401 Unauthorized` against `wss://api.openai.com/v1/responses` — same documented failure, now cleanly reproduced |
| Production platform verdict (real checks, forced, post-fix) | `claude BLOCKED\|CLAUDE_AUTHENTICATION_REQUIRED, codex BLOCKED\|401, android_capture READY, flow_generation BLOCKED` → **`PLATFORM_DEGRADED`** (was `PLATFORM_NOT_READY`) |

Both CLIs support headless API-key auth as an alternative to interactive subscription
login (`claude --bare` + `ANTHROPIC_API_KEY`; `codex login --with-api-key`) — a billing
decision for the operator, not exercised here.

### Deployment

Commits `15c3296` and `4e98ec4` deployed to production via the documented safe-copy
process: backup (`/opt/adforge/backups/pre-phase18-20260828T185622Z`), dry-run diff
review, `rsync` of `src/`/`scripts/`/`schemas/`/`pyproject.toml` only (runtime
directories — `config/`, `data/`, `campaigns/`, etc. — untouched), ownership
correction, `pip install --no-deps /opt/adforge/app` into the venv, a real-environment
import smoke test before restart, `systemctl restart`, clean startup log, and a public
HTTPS smoke test (`200 OK`, logged through Caddy).
