# Operations Runbook

## Health checks
- web/API reachable
- database writable
- storage capacity
- ffmpeg/ffprobe available
- Claude Code auth/health
- Codex CLI auth/health
- Chromium/Flow profile health
- Android SDK/ADB health
- emulator availability
- Caddy handled externally but deployment docs should expose expected upstream port

## Backups
Back up:
- database
- product truth
- campaign manifests/ledgers
- final exports
- configuration excluding recoverable temp files
- secrets via appropriate secure mechanism, not ordinary archive

## Storage pressure
Do not prune automatically. Report usage and request explicit cleanup approval.

## Restart
On service restart, recover durable state and identify the single active campaign. Resume idempotently.

Implemented startup recovery converts interrupted `RUNNING` tasks back to `PENDING`
when attempts remain, preserves completed tasks, and reports the durable active lease.

## Commands

```bash
python3 scripts/environment_doctor.py
sudo systemctl restart adforge
sudo journalctl -u adforge --since today
caddy validate --config deploy/Caddyfile
```

Metadata backups use SQLite's online backup API and archive product/campaign/export
records without secrets, logs, temp files, or browser profiles. Restore is allowed
only into an empty target and rejects unsafe archive paths. Storage reporting never
deletes data; cleanup always requires explicit owner confirmation.
