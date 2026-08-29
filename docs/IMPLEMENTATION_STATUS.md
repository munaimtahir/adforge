# Implementation Status

Last updated: 2026-08-29 UTC.

## Current phase

Phase 19 (real STRATEGY..EXPORT campaign-stage handlers) is complete and deployed to
production. Before this phase, `CampaignWorker` only
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

**Update, same session (see `docs/BLOCKERS.md` for full evidence and commit-by-commit
detail — this is a summary, not a duplicate):**

- B-007 fully resolved: both Claude and Codex authenticated for real on production;
  platform verdict `PLATFORM_READY` (first time ever for this project).
- The real DemoTask campaign (`a0d5338a-4535-4279-9aff-2746593d5add`, product
  `demotask`) ran through the actual production web app, not a fixture: real Product
  Truth validation, real Claude/Codex Strategy/Script/Storyboard/Asset-Plan calls (one
  genuine self-correcting retry on a script timing overlap), real `WorkerJob`
  dispatch. This single real run surfaced and got real fixes for five internal gaps
  no test suite had caught, because nothing had ever driven a real campaign through
  the live app before: `APKIngestor` had zero callers; `ADFORGE_IMPORT_ROOT` was
  declared but never read; there was no way to register any product but
  `warranty-vault`; AI-produced claims needed explicit verbatim-or-omit prompting
  discipline; `/tasks/{id}/retry` created a row `CampaignWorker` could never
  discover. All fixed, tested, deployed.
- Investigated and closed out browser-automated Flow generation as a dead end:
  Google's own anti-automation controls block it at three independent, deliberate
  layers (verified live) — not something to bypass. Built two real alternatives
  instead: Gemini API (Veo) direct generation (`GEMINI_API_KEY`, no browser
  automation), and a manual worker-job completion UI (paste the AI-generated prompt
  into Flow yourself, upload the result — completes through the identical
  claim/store/complete path a real worker uses). Also added real browser-based APK
  upload for campaign creation (previously a server-side path only).
- Deployed commit: see `DEPLOYED_COMMIT.txt` on production, or `git log` for the
  running list of commits this session (`bb80b31` through `fa3c1b9` at last count).
  132 tests pass, ruff/mypy clean throughout.

**Update, same session — DemoTask reached a genuine `COMPLETE` (2026-08-29):**

Campaign `a0d5338a-4535-4279-9aff-2746593d5add` finished the entire pipeline for
real, `PRODUCT_TRUTH_VALIDATION` through `EXPORT`, producing an actual playable
15s 1080×1920 H.264+AAC MP4 (`renders/final/final.mp4`) with `QCResult.passed=True`
and zero blockers/advisories — this project's first real (non-fixture) run to reach
a terminal `COMPLETE` state. Both `flow_generation` jobs were completed by hand
(real prompts pasted into a real Flow session) per explicit instruction, not
automated. Getting from there to `EXPORT` surfaced two more real bugs (full detail
in `BLOCKERS.md`'s "Real DemoTask campaign reached COMPLETE end to end" section):
`asset-plan`'s AI context never included the storyboard's actual scene ids, so two
independent AI calls silently drifted on scene naming (`8f2f4b6`); and
`adb shell screenrecord` returned a well-formed but empty capture on this app's
static screen because Android's encoder only emits frames on visual change — fixed
by injecting real on-screen touches during a backgrounded recording (`a4775b8`,
`09e310a`). Also added the requested worker-job "parameters" UI section (length,
aspect ratio, a 720p-recommended/360p-acceptable quality note) shown separately
from the prompt text (`80d955a`). All fixed, tested (138 tests pass), deployed to
production and re-verified live end to end.

Release verdict remains **ADFORGE v1 — NOT READY** for the canonical Warranty Vault
campaign specifically (B-001: Product Truth/APK/brand assets still absent — independent
of everything above, needs real Warranty Vault data, not a technical blocker). No final
Warranty Vault MP4 has been produced. But the underlying pipeline itself is now proven
end to end against a real, non-fixture campaign — Warranty Vault is blocked on real
product data, not on any remaining technical gap in the pipeline.

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
| 19 — Real STRATEGY..EXPORT campaign-stage handlers; campaign engine autonomy | Complete and deployed; real (non-fixture) end-to-end acceptance achieved | `src/adforge/campaign_stages.py` adds real `StageHandler`s for `PRODUCT_TRUTH_VALIDATION`, `STRATEGY`, `SCRIPT`, `STORYBOARD`, `ASSET_PLAN` (plus its generated `GENERATION_REQUEST.json` dispatch), `AUDIO_PRODUCTION`, `EDIT_PLAN`, `DRAFT_RENDER`, `QC`, `REPAIR`, `FINAL_RENDER`, `EXPORT`, registered in `WebContext` alongside the existing `APP_CAPTURE`/`ASSET_GENERATION` worker handlers — closing the gap where every one of those states had no handler and would immediately `BLOCK`; also fixes `web.py`'s `start`/`resume` routes, which transitioned campaign state but never called `CampaignWorker.run()`, so campaigns could not progress autonomously at all before this. Adds `Campaign.target_duration_seconds` (additive). Tests using a scripted offline `ReasoningProvider` (matching the existing `test_providers.py` stubbing convention) drive a full synthetic campaign through every real handler — real FFmpeg render, real audio synthesis, real ffprobe-validated QC, a genuine QC-failure → `REPAIR` → QC-pass recovery cycle. Security-reviewed: no high/medium-confidence findings (all new AI-influenced identifiers are schema-constrained before reaching any filesystem path, no new subprocess calls, no new cross-campaign query). Deployed to production; real acceptance proven for real against genuine Claude/Codex, and against a real Android emulator capture and real manually-completed Flow assets — the DemoTask campaign (`a0d5338a-4535-4279-9aff-2746593d5add`) reached a genuine `COMPLETE` with a real playable final MP4; see `BLOCKERS.md` for the fixes that took it there (`8f2f4b6`, `a4775b8`, `09e310a`, `80d955a`). 138 tests total pass, ruff and strict mypy clean |

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
| 19 | `bb80b31`, `8f2f4b6`, `a4775b8`, `09e310a`, `80d955a` |

## Baseline quality commands

```bash
python3 scripts/environment_doctor.py
python3 -m pytest
ruff check .
git diff --check
```

This status is intentionally conservative. No integration is considered proven by
the presence of a binary alone, and no mock is acceptance evidence.

## Creative Quality 2.0 Codex sprint (2026-08-29)

Implementation is additive and ready for independent Claude acceptance. Typed CQ2
contracts, canonical-ID validation, Android action DSL validation/execution boundary,
creative planning QC, targeted repair mapping, and explicit master/delivery render
controls are implemented and locally tested. Production deployment, external-worker
V2 capture, and the final DemoTask V1-versus-V2 comparison remain pending.
