# Warranty Vault Acceptance Attempt

Campaign ID: `f708b042-7310-4fb1-952e-f7882d8ad79e`

Attempt date: 2026-08-28 UTC.

Verdict: **ADFORGE v1 — NOT READY**

## Real web evidence

- Campaign created through the authenticated desktop web control plane: HTTP 303.
- Product selected: Warranty Vault.
- Canonical 20-second 9:16 acceptance brief stored with the campaign.
- Production start attempted through the authenticated campaign-detail action.
- Start rejected with HTTP 409 because Product Truth readiness is `UNKNOWN`.
- Durable campaign state remained `CREATED`; active lease remained `false`.
- Runtime metadata database: `.adforge-runtime/acceptance-attempt/data/adforge.sqlite3`.
- UI screenshot (generated evidence, intentionally ignored by Git):
  `docs/10-acceptance/evidence/f708b042-7310-4fb1-952e-f7882d8ad79e/campaign-blocked.png`.

This is the correct hard-gate behavior. No strategy, script, storyboard, app footage,
media generation, audio, edit, QC, or final render was attributed to Warranty Vault.

## Strongest independent integration evidence

The automated suite separately proves, without calling it Warranty Vault acceptance:

- real authenticated Claude Code and Codex structured adapter calls;
- generation and emulator handoff export/import/resume using representative fixture returns;
- original local test audio generation and non-clipping mix;
- real FFmpeg MP4 render and ffprobe inspection;
- broken-render QC, unsupported-claim rejection, and targeted repair budget;
- durable worker stop/restart, external wait/resume, repair loop, and completion;
- one-active-campaign enforcement and immutable Product Truth snapshots.

Fixture and contract evidence is not substituted for missing authoritative Warranty
Vault truth, APK, authentic app capture, or real Flow output.
