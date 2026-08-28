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
  +-- Worker API (/api/worker/*, bearer-token auth, distributed job leasing)
  |
Local Persistent Filesystem

Outbound HTTPS only, no inbound port on the worker side:

AdForge Web/API <---- heartbeat/claim/lease/artifact/complete ---- External Worker
                                                                      +-- android_capture
                                                                      +-- flow_generation
                                                                      +-- synthetic_echo
```

A distributed worker (e.g. an Android-capable Windows laptop) is a capability
provider, not part of the control plane. It never receives an inbound connection
and never runs on the VM. See `docs/02-architecture/WORKER_PROTOCOL.md` for the
registration/heartbeat/claim/lease/artifact/complete contract, and
`docs/08-security/SECURITY_MODEL.md` for the worker authentication model. A worker
being offline degrades only the capability it provided
(`android_capture`/`flow_generation`); it does not make the platform `NOT_READY` —
a campaign that needs that capability waits (`WAITING_FOR_WORKER`) instead of
failing.

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
- The VM is the authoritative control plane; distributed workers provide external
  capabilities (Android capture, Flow browser generation) and connect outbound only.
- Orchestrator owns state and retries.
- AI providers return task outputs; they do not own workflow state.
- Provider adapters expose stable internal contracts.
- Renderer receives a machine-readable edit specification.
- Product Truth is snapshotted at campaign start.
- Campaign workspaces are immutable-by-default provenance containers; new versions supersede rather than silently overwrite important artifacts.
