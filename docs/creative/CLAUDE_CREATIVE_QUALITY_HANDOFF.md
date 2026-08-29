# Claude Creative Quality 2.0 Handoff

Status: **IMPLEMENTATION PARTIALLY COMPLETE — READY FOR CLAUDE PRODUCTION ACCEPTANCE**

## A. Repository state

- Starting branch/commit: `main` / `7711f5d`
- Ending branch/commit: `main` / `bee265c` (implementation commit `fcd18d2`, followed by this handoff commit)
- Upstream: `origin/main`; local branch is one commit ahead after Codex implementation.
- Codex left no uncommitted changes before adding this handoff.

## B–P. Implementation summary and architecture

Codex added `src/adforge/creative_quality.py`, an additive CQ2 contract layer. It defines typed/versioned strategy, script beats/channels, storyboard shots, capture instructions, Android actions, composition, typography, transitions, edit plans, QC signals/results, repair instructions, capture scenarios, and export profiles. Public short aliases are provided as `CreativeStrategy`, `ScriptPlan`, `Storyboard`, `EditPlan`, and `CreativeQCResult` in that module; V1 `adforge.creative` contracts remain untouched.

Strategy includes audience insight/tension, objective, proposition, benefit/proof, hook, angles, visual thesis, CTA, pacing/energy, shot count, generated/real balance, raw-UI tolerance, avoidances, claims, audio, typography, and continuity direction. Script channels distinguish narration, overlay, product UI, sound design, silence, and CTA; exact normalized narration/overlay echoes are rejected. Storyboard shots are contiguous and require unique canonical shot/scene IDs with explicit visual source, purpose, product state, composition, text, transitions, audio, capture, keyboard, and QC intent.

Canonical references are checked by `validate_canonical_ids` and `validate_storyboard_asset_ids`; unknown downstream IDs fail rather than being renamed or remapped. Product Truth integration remains the existing exact-evidence validator in `CreativePipeline`/`QCService`; no bypass was introduced.

Android DSL operations: WAIT, TAP, TAP_TEXT, TAP_COORDINATE, TYPE_TEXT, CLEAR_TEXT, SWIPE, BACK, HOME, HIDE_KEYBOARD, SHOW_KEYBOARD, SCREENSHOT, ASSERT_VISIBLE, ASSERT_NOT_VISIBLE, ASSERT_PACKAGE, HOLD. `AndroidAction` rejects inappropriate fields, control characters, unsafe capture filenames, and invalid shapes. `AndroidActionExecutor` has no shell field or arbitrary-command path; it accepts only a narrow adapter protocol, bounds coordinates, validates screenshot names, and emits explicit failure codes. Existing `ADBAdapter` remains the real subprocess boundary and its prior safe argument-array behavior is preserved.

Keyboard policies are `REQUIRED`, `ALLOWED`, and `FORBIDDEN`; the policy is recorded on capture instructions and shots. Capture scenarios support FRESH_INSTALL, CLEAN_STATE, PREPOPULATED_STATE, EXISTING_TASK, COMPLETED_TASK, and FEATURE_SCREEN_READY with preparation and verification actions.

Composition supports `RAW_FULL_SCREEN` and `DEVICE_FRAME`, with frame/mask/background/shadow/safe-margin fields. Typography roles are HEADLINE, BENEFIT, CAPTION, CTA, BRAND_LOCKUP, and DISCLAIMER. Transitions are typed enums, never arbitrary FFmpeg prose. Editing patterns are HOOK_CUT, PRODUCT_REVEAL, FEATURE_PROOF, BENEFIT_CUTAWAY, PRODUCT_CONFIRMATION, and CTA_HOLD.

The existing renderer now accepts explicit `export_kind`, CRF, preset, audio bitrate, and faststart settings and passes them through safe FFmpeg argv. CQ2 defaults are a 1080×1920 H.264/AAC medium/CRF18 master and a CRF23/veryfast/128k delivery profile. Existing V1 render behavior remains compatible through defaults.

Creative QC currently provides deterministic planning checks for raw UI fraction, source variety, product proof, and CTA hold, with measurement, threshold, affected IDs, evidence, and repair stage. The complete signal vocabulary and repair map are defined. Existing technical/truth QC and bounded repair engine remain authoritative. Repair map: keyboard→APP_CAPTURE; raw UI/proof→STORYBOARD; duplicate text/collision→SCRIPT/TYPOGRAPHY/EDIT_PLAN; CTA→EDIT_PLAN; continuity→COMPOSITION/EDIT_PLAN; audio→AUDIO_PRODUCTION; unsupported claim→STRATEGY/SCRIPT.

## Q. Tests and gates

- `.venv/bin/pytest -q`: **143 passed**, one existing Starlette/httpx deprecation warning.
- `.venv/bin/ruff check .`: **PASS**.
- `.venv/bin/mypy src/adforge`: **PASS**, strict, 28 source files.
- `python3 scripts/secret_scan.py`: **PASS**.
- `git diff --check`: **PASS**.
- `python3 scripts/environment_doctor.py`: required capabilities ready; ADB reports `devices=0`, emulator unavailable; FFmpeg/ffprobe available.
- New `tests/test_creative_quality.py`: 5 tests covering canonical IDs, contiguous timing, device-frame requirements, script redundancy, Android injection/bounds, QC and repair mapping.
- Existing real-media renderer tests ran as part of the full suite; FFmpeg/ffprobe fixture render and text escaping passed.

## R. Real runtime tests

No real directed Android capture was possible locally: this development VM has ADB but zero devices and no emulator binary/profile. Prior V1 evidence for real external-worker Android capture and production MP4 remains preserved in `docs/TEST_AND_RUNTIME_EVIDENCE.md`. No new production campaign, external worker V2 capture, Flow generation, or deployment was performed.

## S–T. Unverified assumptions and intentionally unperformed work

Claude must independently verify provider compatibility, migration/backward compatibility of any persisted CQ2 payloads, worker payload wiring, real TAP_TEXT/assertion/keyboard adapter implementations, device-frame FFmpeg filters, typography layer integration, semantic AI review, full QC signal extraction from rendered media, and campaign-state targeted invalidation. No database migration was created. Production files, secrets, cookies, browser profiles, APKs, or V1 artifacts were not modified.

## U. Required Claude acceptance checklist

1. Inspect all diffs and commits; rerun pytest, Ruff, strict mypy, secret scan, and media tests.
2. Audit schemas, migrations, canonical IDs, Product Truth gates, worker compatibility, and asset isolation.
3. Complete real external-worker directed Android capture; verify keyboard policy and Flow generation.
4. Wire and verify device composition, typography, typed transitions, CQ2 EditPlan, semantic review, and all advertised QC signals.
5. Run real master and delivery exports; inspect ffprobe output and manifests/provenance.
6. Back up production, dry-run and deploy through the copy-based process, restart, inspect logs, and verify public HTTPS.
7. Create a new DemoTask V2 campaign while preserving V1; run QC, targeted repairs, master export, contact sheets, objective metrics, and visual review.
8. Determine IMPROVED/EQUIVALENT/REGRESSED, update release docs/ledger, and issue the final platform verdict. Do not call CQ2 production-complete before these steps.
