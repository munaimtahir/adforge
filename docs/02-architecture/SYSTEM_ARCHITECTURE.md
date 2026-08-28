# System Architecture

## Topology

```text
Browser
  |
Caddy (external concern: TLS/reverse proxy)
  |
AdForge Web/API
  |
Production Orchestrator
  +-- State Store / Campaign DB
  +-- Product Truth Service
  +-- Asset/Manifest Service
  +-- Provider Router
  |    +-- Claude Code Adapter
  |    +-- Codex CLI Adapter
  |    +-- Flow Browser Adapter
  |    +-- Future Provider Adapters
  +-- Android Capture Service
  +-- Audio Service
  +-- Renderer Adapter
  |    +-- FFmpegRenderer (v1)
  +-- QC Service
  +-- Handoff Service
  +-- Production Ledger
  |
Local Persistent Filesystem
```

## Recommended implementation baseline
Technical details may change if tests prove a better choice:
- Python 3.12+
- FastAPI backend
- Server-rendered/lightweight frontend or React-based frontend, chosen during Phase 1
- SQLite for v1 unless concurrency/state requirements justify PostgreSQL
- Background worker implemented without unnecessary distributed infrastructure
- Pydantic schemas
- FFmpeg/ffprobe
- Playwright + persistent Chromium profile for Flow adapter
- Android SDK + ADB + emulator
- Maestro/UIAutomator where useful
- Git

## Architectural boundaries
- Orchestrator owns state and retries.
- AI providers return task outputs; they do not own workflow state.
- Provider adapters expose stable internal contracts.
- Renderer receives a machine-readable edit specification.
- Product Truth is snapshotted at campaign start.
- Campaign workspaces are immutable-by-default provenance containers; new versions supersede rather than silently overwrite important artifacts.
