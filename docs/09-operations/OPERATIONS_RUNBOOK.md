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
