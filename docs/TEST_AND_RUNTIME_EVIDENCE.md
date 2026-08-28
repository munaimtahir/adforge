# Test and Runtime Evidence

Audit date: 2026-08-28 UTC.

## Final automated gates

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
