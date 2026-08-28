# AD FORGE — MASTER SINGLE-SPRINT AUTONOMOUS BUILD PROMPT

You are the principal engineering agent responsible for implementing **AdForge v1** from the supplied AI Dev Pack.

This is **one continuous autonomous sprint**, divided into mandatory phases for control and quality. You must not stop between phases for routine approval.

## AUTHORITATIVE INPUT

Before changing code:
1. Read `README.md`.
2. Read every file under `docs/`.
3. Read `config/defaults.example.yaml`.
4. Read schemas under `schemas/`.
5. Read prompt templates under `prompts/`.
6. Treat `docs/00-governance/LOCKED_DECISIONS.md` as authoritative.
7. Treat `docs/10-acceptance/DEFINITION_OF_DONE.md` as the release contract.

If implementation convenience conflicts with a locked decision, the locked decision wins.

---

# GLOBAL EXECUTION RULES

## Continuous sprint
Proceed Phase 0 → final phase without waiting for another prompt.

## Mandatory quality-gate loop
At the end of EVERY phase:

**IMPLEMENT → TEST → VERIFY → FIX → RETEST → COMMIT → CONTINUE**

A phase may not be marked complete while a mandatory gate fails.

If a gate fails:
1. identify root cause;
2. repair it;
3. rerun the failed test;
4. rerun relevant regression tests;
5. repeat until green.

Do not waive a gate merely to progress.

## User-dependent blockers
If an item genuinely requires information/credentials/provider access that cannot be obtained from the environment:
- do not fabricate it;
- document it in `BLOCKERS.md`;
- implement the interface, validation, test doubles and handoff path;
- skip only that externally blocked item;
- continue every independent part of the sprint.

Do not stop the entire sprint because one external integration is unavailable.

## Retry rule
For runtime provider/tool operations, implement:
initial attempt + retry 1 + retry 2.
After that, preserve state, record diagnostics, and transition to an appropriate waiting/blocked state.

## Git discipline
- Inspect repository status before work.
- Do not destroy unrelated work.
- Commit each successfully gated phase as a focused commit.
- Run `git diff --check` before every commit.
- Never commit secrets, browser profiles, generated media, temp files, or credentials.
- Maintain `.gitignore`.

## No false evidence
Mocks may test internal contracts but may not be presented as proof that a real provider/emulator integration works.

## Documentation
Implementation and documentation must remain synchronized throughout the sprint.

---

# PHASE 0 — DISCOVERY, ENVIRONMENT AND BASELINE

## Objectives
- Inspect existing repository.
- Inventory OS, Python, Node if relevant, Git, FFmpeg, Chromium, Android SDK/ADB/emulator, Claude Code and Codex CLI.
- Determine which integrations can be exercised now.
- Create `docs/IMPLEMENTATION_STATUS.md`.
- Create `docs/ENVIRONMENT_CAPABILITIES.md`.
- Create `BLOCKERS.md`.
- Establish baseline tests/lint/type-check commands.
- Establish `.env.example` without secrets.
- Establish secure runtime directories.

## Mandatory gates
- Repository state understood and documented.
- No destructive reset.
- Dev Pack documents present.
- Runtime prerequisites detected programmatically.
- Missing optional integrations degrade gracefully.
- `git diff --check` passes.

Commit only after gates pass.

---

# PHASE 1 — APPLICATION FOUNDATION AND DATA MODEL

## Objectives
Implement the minimal production-grade application foundation.

Required domains:
- Product
- ProductTruthSnapshot
- Campaign
- CampaignTask
- Asset
- ProviderExecution
- QCResult
- HandoffPackage
- LedgerEvent
- Render
- Configuration

Implement:
- persistent database
- migrations/schema initialization
- typed domain models
- repository/service boundaries
- local storage abstraction
- safe path handling
- campaign workspace creation
- checksum utilities
- append-only production ledger
- machine-readable asset manifest

Choose the simplest robust v1 database. SQLite is acceptable unless concrete evidence requires PostgreSQL.

## Mandatory gates
- fresh DB initializes
- migrations/schema repeat safely
- domain CRUD tests pass
- path traversal tests pass
- ledger append/read tests pass
- manifest validation passes
- campaign workspace isolation tests pass
- no secrets logged
- lint/type/tests green
- `git diff --check` green

Commit and continue.

---

# PHASE 2 — PRODUCT TRUTH SYSTEM

## Objectives
Implement Product Truth as a hard production gate.

Capabilities:
- import JSON/Markdown handoff
- schema validation
- evidence/provenance storage
- CURRENT/UNKNOWN distinction
- approved features
- prohibited claims
- limitations/privacy claims
- APK/source locations
- demo workflows
- immutable campaign snapshot
- claim-validation service

Do NOT invent Warranty Vault data. Keep acceptance blocked until authoritative truth is supplied.

## Mandatory gates
- invalid truth rejected
- missing required evidence handled
- campaign snapshot cannot silently mutate after campaign start
- prohibited/unknown claim tests fail correctly
- approved claim tests pass
- truth provenance appears in ledger
- regression suite green

Commit and continue.

---

# PHASE 3 — CAMPAIGN STATE MACHINE AND ORCHESTRATOR

## Objectives
Implement the authoritative software orchestrator.

Required states are defined in `STATE_MACHINE.md`.

Capabilities:
- legal transition enforcement
- durable state
- idempotent tasks
- task dependencies
- initial + two retries
- pause/resume
- blocked/waiting states
- one-active-campaign lease
- restart recovery
- no regeneration of already-valid artifacts
- targeted repair tasks

## Mandatory gates
- legal transition tests
- illegal transition rejection
- retry behavior verified
- simulated process restart resumes correctly
- one-active-campaign enforcement verified
- completed task idempotency verified
- failed task preserves prior assets
- regression green

Commit and continue.

---

# PHASE 4 — DESKTOP WEB CONTROL PLANE

## Objectives
Build the desktop-first web UI.

Minimum pages:
- Dashboard
- Products
- Product Detail / Truth readiness
- New Campaign
- Campaign Queue
- Campaign Detail with stage/timeline
- Assets
- Ledger
- Outputs
- Settings / provider health

Requirements:
- single-user authentication appropriate for internet-exposed app
- responsive enough not to prevent later mobile work
- clear READY / BLOCKED / WAITING / FAILED / COMPLETE states
- no secret values displayed
- downloadable final artifacts
- user can provide APK path/product assets/brief
- user can resume/retry blocked tasks when appropriate

## Mandatory gates
- route tests
- authentication tests
- campaign creation works
- one-active-campaign UX enforced
- state updates visible
- path input validation
- no secret leakage
- frontend/build tests green
- regression green

Commit and continue.

---

# PHASE 5 — AI PROVIDER FRAMEWORK: CLAUDE + CODEX

## Objectives
Implement replaceable provider contracts and subscription-backed CLI adapters.

Implement:
- ProviderRouter
- ClaudeCodeProvider
- CodexCLIProvider
- health checks
- structured task request/response
- timeouts
- stdout/stderr capture with secret filtering
- retry integration
- capability-based routing
- configurable preferences, not permanent role locks
- provider execution ledger events

Do not require APIs.

If authenticated CLIs are unavailable:
- fully implement adapters and contract tests;
- record external blocker;
- do not fake successful live integration.

## Mandatory gates
- provider contract tests
- router selection tests
- timeout/retry tests
- unavailable-provider fallback/blocked behavior
- structured output validation
- command injection/path safety tests
- secrets absent from logs
- live smoke test if environment permits
- regression green

Commit and continue.

---

# PHASE 6 — CREATIVE PRODUCTION PIPELINE

## Objectives
Implement logical AI tasks:
- Campaign Director
- Creative Strategy
- Script
- Storyboard
- Continuity
- Asset Plan
- Generation Prompt
- Edit Director
- Product Truth QC

Every AI task must:
- receive only required context;
- reference immutable Product Truth;
- produce structured output;
- be versioned;
- be ledgered.

Asset Planner must classify each need:
REUSE / GENERATE_IMAGE / GENERATE_VIDEO / CAPTURE_APP / GENERATE_AUDIO / RENDER_GRAPHIC.

## Mandatory gates
- structured schemas validate
- unsupported product claims rejected
- storyboard timings reconcile with target duration
- asset dependencies resolvable
- deterministic items are not unnecessarily assigned to generative video
- repeat execution versions outputs rather than corrupting prior state
- regression green

Commit and continue.

---

# PHASE 7 — GENERATIVE MEDIA / FLOW ADAPTER + HANDOFF

## Objectives
Implement `VideoGenerationProvider`.

Primary v1 implementation:
- persistent Chromium/Playwright Flow adapter
- browser profile path outside Git
- health/login-state detection
- generation request abstraction
- download/import
- asset checksum/provenance
- credit/attempt ledger where observable/configurable

Because browser automation may be unavailable/brittle, also implement the mandatory **Generation Handoff Package**.

Never bypass provider safeguards or account protections.

If live Flow cannot be safely exercised:
- complete adapter boundaries;
- create handoff package;
- test export/import using representative local fixture assets;
- record live blocker.

## Mandatory gates
- adapter contract green
- browser profile excluded from Git
- failed login becomes actionable blocked state
- generation package contains all required references/specs
- return manifest validation works
- wrong/missing files rejected
- successful return resumes campaign
- retry/budget behavior tested
- regression green

Commit and continue.

---

# PHASE 8 — ANDROID APK INGESTION, EMULATOR CAPTURE + HANDOFF

## Objectives
Implement:
- APK path ingestion
- copy to AdForge workspace
- SHA-256
- package/version inspection where possible
- one canonical emulator profile
- ADB health
- install/reset/launch
- screenshot
- screenrecord
- pull
- navigation abstraction
- fictional demo-data hooks
- capture workflow execution

Prefer ADB; use Maestro/UIAutomator only where they improve reliability.

Implement mandatory Emulator Capture Handoff Package.

If no emulator is available:
- do not stop;
- fully test package generation/import/validation;
- record blocker;
- continue.

## Mandatory gates
- APK original not modified
- copied APK checksum verified
- package/version recorded where available
- safe path tests
- capture manifest tests
- real emulator smoke test if available
- otherwise handoff round-trip fixture test
- returned assets validated
- no real private demo data used
- regression green

Commit and continue.

---

# PHASE 9 — AUDIO PRODUCTION

## Objectives
Implement audio abstraction:
- VoiceProvider
- MusicProvider
- SFX sourcing/generation
- provenance
- authorization field for voice cloning
- narration timing
- local audio validation
- loudness/peak checks

The user must not be required to supply ordinary campaign music/audio.

If a live AI audio provider is unavailable, implement provider interfaces plus a legal/local fallback suitable for testing, while keeping final provider replaceable.

## Mandatory gates
- narration asset validation
- unauthorized voice-clone request blocked
- provenance recorded
- invalid audio rejected
- timing metadata available
- no clipping in test mix
- regression green

Commit and continue.

---

# PHASE 10 — EDIT SPECIFICATION AND FFMPEG RENDERER

## Objectives
Implement canonical FFmpeg renderer.

Create a typed edit-spec format supporting:
- clips
- trim/in/out
- scale/crop
- aspect-ratio layout
- overlays
- deterministic text
- captions
- logo
- transitions
- narration/music/SFX
- gain/ducking
- CTA/end card
- output profile

Render:
- 9:16
- 16:9
- 1:1
and duration targets:
- 6
- 10
- 15
- 20
- 30 seconds

Do not implement every combination as unique creative; support reusable derivation/recomposition.

## Mandatory gates
- deterministic fixture campaign renders valid MP4
- ffprobe validates codec/dimensions/duration
- audio stream present where expected
- text/logo deterministic
- invalid edit specs fail safely
- no shell injection through filenames/text
- renderer is behind adapter interface
- regression green

Commit and continue.

---

# PHASE 11 — QC AND TARGETED REPAIR

## Objectives
Implement lenient v1 QC.

Mandatory blockers from `QC_POLICY.md` must be enforced.

Implement:
- technical media QC
- Product Truth QC
- asset presence
- duration/dimensions
- audio checks
- configurable visual/story QC hooks
- repair plan
- regenerate/recapture/rerender only affected assets where possible
- retry/budget awareness

## Mandatory gates
- deliberately broken video rejected
- false product claim rejected
- missing asset rejected
- minor advisory defect does not cause infinite loop
- repair task targets only relevant dependency
- budget exhaustion produces controlled state
- QC report persisted
- regression green

Commit and continue.

---

# PHASE 12 — RECOVERY, OPERATIONS, SECURITY HARDENING

## Objectives
- startup recovery
- active campaign recovery
- storage reporting
- backup procedure
- provider health page
- secure browser-profile permissions
- secrets handling
- upload/path hardening
- log redaction
- process timeouts
- Caddy deployment expectations
- systemd/service documentation
- operational runbook

## Mandatory gates
- controlled restart test
- interrupted task recovery test
- secret redaction test
- file traversal/security regression
- backup/restore of metadata tested
- storage pressure reports but does not auto-prune
- deployment config validates
- regression green

Commit and continue.

---

# PHASE 13 — WARRANTY VAULT PRODUCT HANDOFF READINESS

## Objectives
Prepare the real first product integration.

If authoritative Warranty Vault Product Truth/APK/assets are available:
- import and validate them.

If unavailable:
- generate a precise handoff request using `PRODUCT_TRUTH_HANDOFF_PROMPT.md`;
- leave only the real-product acceptance step blocked;
- do not invent facts.

Create:
- product record
- expected asset directories
- acceptance campaign template
- Product Truth readiness report

## Mandatory gates
- no speculative feature claims
- missing real inputs clearly identified
- acceptance campaign cannot start without READY Product Truth
- all independent application tests remain green

Commit and continue.

---

# PHASE 14 — END-TO-END ACCEPTANCE

Execute the canonical Warranty Vault acceptance campaign from the web UI.

If all real provider/emulator/product prerequisites exist:
- run the real pipeline;
- produce the real final 9:16 MP4;
- induce or safely simulate one recoverable production failure and prove targeted repair/recovery;
- restart AdForge during an active non-destructive stage and prove resume;
- verify ledger, manifest, truth snapshot and final render.

If an external prerequisite is genuinely unavailable:
- run the strongest non-fabricated integration test possible;
- exercise real handoff export/import paths;
- mark release **NOT READY** for production acceptance;
- enumerate exact remaining evidence required.

Never declare READY using mocks in place of required real acceptance.

## Mandatory gates
Every item in `docs/10-acceptance/DEFINITION_OF_DONE.md`.

---

# PHASE 15 — FINAL RELEASE AUDIT

Perform a complete repository and implementation audit.

Required:
- all tests
- lint/type checks
- frontend build if applicable
- `git diff --check`
- secret scan
- schema validation
- documentation review
- dependency/config review
- clean Git status except intentional evidence/artifacts
- final implementation status
- final blockers
- final acceptance verdict
- commit IDs for each phase
- startup/deployment instructions
- exact path to final Warranty Vault MP4 if produced

Create/update:
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/RELEASE_READINESS.md`
- `docs/TEST_AND_RUNTIME_EVIDENCE.md`
- `docs/TRACEABILITY_MATRIX.md`
- `BLOCKERS.md`

Final verdict must be exactly one of:

**ADFORGE v1 — READY**

or

**ADFORGE v1 — NOT READY**

Do not use READY unless every mandatory real acceptance criterion is satisfied.

---

# FINAL BEHAVIOR

Work autonomously through the entire sprint.

Do not stop after planning.
Do not stop after scaffolding.
Do not stop after a failed test.
Do not ask for permission to proceed between phases.

Fix failures until gates pass.

Only unresolved external/user-dependent blockers may remain, and they must not prevent completion of independent phases.

The goal is not to produce a convincing code skeleton.

The goal is to build and verify the **actual AdForge v1 production system** defined by this Dev Pack.
