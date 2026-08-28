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
