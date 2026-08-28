# Implementation Status

Last updated: 2026-08-29 UTC.

## Current phase

Phase 19 (real STRATEGY..EXPORT campaign-stage handlers) is complete and deployed
locally (not yet to production — see below). Before this phase, `CampaignWorker` only
had real handlers for `APP_CAPTURE` and `ASSET_GENERATION`; every other primary state
(`PRODUCT_TRUTH_VALIDATION`, `STRATEGY`, `SCRIPT`, `STORYBOARD`, `ASSET_PLAN`,
`AUDIO_PRODUCTION`, `EDIT_PLAN`, `DRAFT_RENDER`, `QC`, `REPAIR`, `FINAL_RENDER`,
`EXPORT`) had no handler registered in `WebContext`, so any campaign reaching one of
them would immediately `BLOCK` — despite the underlying library code
(`CreativePipeline`, `AudioService`, `FFmpegRenderer`, `QCService`/`RepairPlanner`)
already being complete and independently tested. `src/adforge/campaign_stages.py`
wires all twelve as real `StageHandler`s. Separately, `web.py`'s `/campaigns/{id}/start`
and `/resume` routes transitioned campaign state but never actually invoked
`CampaignWorker.run()` — so no campaign could progress past its very first transition
without a human manually driving each stage; this is now fixed. `tests/test_campaign_stages.py`
proves the full wired pipeline end to end with real FFmpeg/audio/QC and a scripted
(offline) AI-provider boundary, including a real QC-failure → REPAIR → QC-pass
recovery, producing an actual playable 1080×1920 MP4 — see its own module docstring.
Commit `bb80b31`.

Release verdict remains **ADFORGE v1 — NOT READY** for the canonical Warranty Vault
campaign specifically (B-001: Product Truth/APK/brand assets absent; B-003/B-006: Flow
authentication needs one human interactive login). The distributed worker subsystem
itself is genuinely proven end to end against production (B-002/B-004/B-005 resolved;
B-007 installation/PATH fixed, interactive login now in progress as of this session —
see `docs/BLOCKERS.md`). No final Warranty Vault MP4 was produced. Phase 19 is
implemented and locally tested but **not yet deployed to production and not yet
exercised as a real end-to-end campaign against genuine Claude/Codex/Flow/Android** —
that real-acceptance run is the next step, pending the in-progress production
Claude/Codex logins and local Flow login (this session).

## Phase ledger

| Phase | Status | Evidence |
|---|---|---|
| 0 — Discovery/environment/baseline | Complete | Environment doctor, capability report, blockers, baseline test |
| 1 — Application foundation/data model | Complete | Domain CRUD, migration, path, workspace, manifest, checksum, ledger tests |
| 2 — Product Truth | Complete | Import, schema/evidence, immutable snapshot, claim and provenance tests |
| 3 — State machine/orchestrator | Complete | Transition, retry, restart, lease, idempotency and repair tests |
| 4 — Desktop web control plane | Complete | Route, auth, CSRF, campaign, lease UX, path and secret tests |
| 5 — Claude/Codex providers | Complete | Contract, router, retry, schema, injection, redaction and live smoke evidence |
| 6 — Creative production pipeline | Complete | Role schema, claim, timing, dependency, routing and version tests |
| 7 — Flow/video generation + handoff | Complete except live external smoke | Adapter and fixture handoff round-trip tests; Chromium/login blocker recorded |
| 8 — Android/APK/emulator + handoff | Complete except live external smoke | APK, parser, safety, fictional data, manifest and fixture round-trip tests |
| 9 — Audio production | Complete | Clone authorization, validation, timing, provenance, mix/peak tests |
| 10 — Edit spec/FFmpeg renderer | Complete | Real MP4/ffprobe, profile, text, audio, invalid spec and injection tests |
| 11 — QC and targeted repair | Complete | Broken media, claim, missing asset, advisory, targeted repair and budget tests |
| 12 — Recovery/operations/security | Complete | Restart, backup, storage, redaction, permissions and deployment validation tests |
| 13 — Warranty Vault readiness | Complete except real handoff | Claim-free seed, paths, request/report, and READY start-gate tests |
| 14 — End-to-end acceptance | Strongest path complete; real acceptance blocked | Real web attempt plus UI screenshot; fixture worker/restart/handoff/repair/render evidence; Warranty Vault truth/APK, emulator, and Flow access absent |
| 15 — Release audit | Complete | Tests/lint/types/schemas/config/deploy/secret scan and release documents; commit `80da668` |
| 16 — Distributed worker foundation | Complete; real external worker/Android/Flow acceptance blocked | Worker domain/auth/API/UI/agent, real capability health checks, synthetic `synthetic_echo` round trip proven via `tests/test_worker.py` and over real HTTP against a running dev server (register → heartbeat → claim → lease → checksum-validated artifact upload → idempotent complete → `WAITING_FOR_WORKER` auto-resume); see `docs/02-architecture/WORKER_PROTOCOL.md` |
| 17 — Android/Flow worker pipelines | Implementation complete; real acceptance blocked (B-004/005/006) | `scripts/worker_agent.py` real Android SDK/AVD discovery + capture pipeline and real Flow login/generation pipeline (argument-array-only subprocess calls); server-side lease-gated `GET /api/worker/jobs/{id}/inputs/{filename}`; the `synthetic_echo` round trip additionally proven against live production `https://adforge.vexel.pk` over real public HTTPS; Android SDK discovery and Flow login-state detection verified for real (no mocks) against this VM's actual device-less SDK and the live Flow site; discovered and recorded new blocker B-007 (production Claude/Codex CLI not usable by the `adforge` service account) via the real-invocation health checks introduced in phase 16 |
| 18 — Campaign → WorkerJob orchestration; real external worker acceptance | Complete; B-002/B-004/B-005 resolved for real; B-007 installation/PATH fixed (auth is the one remaining human action) | `src/adforge/worker_stages.py` wires real `APP_CAPTURE`/`ASSET_GENERATION` handlers into `CampaignWorker`, which is now actually instantiated in `WebContext` for the first time; automatic `WorkerJob` dispatch with durable `WAITING_FOR_WORKER` (never fails the campaign), artifact import on completion, and continued auto-advancement, all with an explicit opt-in manual-handoff fallback (`ADFORGE_MANUAL_HANDOFF_STAGES`); 11 new tests (`tests/test_worker_stages.py`) covering payload correctness, claim matching, no-worker-online waiting, artifact import + auto-resume, duplicate-completion safety, retry/failure, restart persistence, manual fallback, Product Truth gate untouched; deployed to production (commit `4e98ec4`); real second machine `adforge-linux-01` registered and proved the full protocol against production; real `android_capture` WorkerJob executed end to end (real canonical-AVD install/launch/capture, VM-side ffprobe-validated); real lease-crash/reclaim/retry proven live; fixed a real canonical-AVD resolution bug (emulator was booting at `1080x2400` instead of the documented `1080x1920`); fixed a real production bug (a stale `active=True` campaign lease from an earlier session silently blocking all campaign resumption); fixed Claude Code CLI PATH/installation for the `adforge` service account (`sudo npm install -g @anthropic-ai/claude-code`), moving the platform health verdict from `PLATFORM_NOT_READY` to `PLATFORM_DEGRADED` |
| 19 — Real STRATEGY..EXPORT campaign-stage handlers; campaign engine autonomy | Complete locally; not yet deployed; real (non-fixture) end-to-end acceptance still blocked on B-003/B-006/B-007 | `src/adforge/campaign_stages.py` adds real `StageHandler`s for `PRODUCT_TRUTH_VALIDATION`, `STRATEGY`, `SCRIPT`, `STORYBOARD`, `ASSET_PLAN` (plus its generated `GENERATION_REQUEST.json` dispatch), `AUDIO_PRODUCTION`, `EDIT_PLAN`, `DRAFT_RENDER`, `QC`, `REPAIR`, `FINAL_RENDER`, `EXPORT`, registered in `WebContext` alongside the existing `APP_CAPTURE`/`ASSET_GENERATION` worker handlers — closing the gap where every one of those states had no handler and would immediately `BLOCK`; also fixes `web.py`'s `start`/`resume` routes, which transitioned campaign state but never called `CampaignWorker.run()`, so campaigns could not progress autonomously at all before this. Adds `Campaign.target_duration_seconds` (additive). 4 new tests (`tests/test_campaign_stages.py`) using a scripted offline `ReasoningProvider` (matching the existing `test_providers.py` stubbing convention) drive a full synthetic campaign through every real handler — real FFmpeg render, real audio synthesis, real ffprobe-validated QC, a genuine QC-failure → `REPAIR` → QC-pass recovery cycle — producing an actual playable 1080×1920 MP4 with audio via `CampaignWorker` end to end; 125 tests total pass, ruff and strict mypy clean; commit `bb80b31`. Security-reviewed: no high/medium-confidence findings (all new AI-influenced identifiers are schema-constrained before reaching any filesystem path, no new subprocess calls, no new cross-campaign query). Not yet deployed to production; real acceptance against genuine Claude/Codex/Flow/Android still requires B-007 (production Claude/Codex login) and B-003/B-006 (Flow login) |

## Phase commits

| Phase | Commit |
|---:|---|
| 0 | `07c77f0` |
| 1 | `36d5a3b` |
| 2 | `3185745` |
| 3 | `0e19e3c` |
| 4 | `dfc6439` |
| 5 | `5581814` |
| 6 | `0d1ad8a` |
| 7 | `91324e1` |
| 8 | `2419c4d` |
| 9 | `3c1f250` |
| 10 | `9227a5e` |
| 11 | `8282cf2` |
| 12 | `d569a7a` |
| 13 | `621169e` |
| 14 | `f848e9b` |
| 15 | `80da668` |
| 16 | `b307939` |
| 17 | `3ca7609` |
| 18 | `15c3296`, `4e98ec4` |
| 19 | `bb80b31` |

## Baseline quality commands

```bash
python3 scripts/environment_doctor.py
python3 -m pytest
ruff check .
git diff --check
```

This status is intentionally conservative. No integration is considered proven by
the presence of a binary alone, and no mock is acceptance evidence.
