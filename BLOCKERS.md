# External Blockers

Last reviewed: 2026-08-28 UTC.

These blockers affect live integration or real-product acceptance only. Independent
implementation and handoff contract work continues.

| ID | Blocker | Required resolution/evidence | Affected acceptance |
|---|---|---|---|
| B-001 | Authoritative Warranty Vault Product Truth, current APK, and brand assets are absent | Return every item in `docs/10-acceptance/WARRANTY_VAULT_HANDOFF_REQUEST.md`; AdForge must validate it and set the product to READY | Real Product Truth gate and Warranty Vault campaign |
| B-002 | No Android AVD or connected device is configured | Configure one canonical emulator/device and prove install, reset, launch, capture, and pull against the supplied APK | Authentic app footage |
| B-003 | Chrome/Playwright run, but the persistent profile lacks proven Flow generation access | Authenticate a Flow-capable subscription in the mode-0700 profile and pass a real generate/download/import smoke test | Live Flow/Veo generation |

## Release impact

All three blockers are required for the canonical real-product acceptance. Until they
are resolved and the evidence in `docs/RELEASE_READINESS.md` is produced, the verdict
remains **ADFORGE v1 — NOT READY**. There is no final Warranty Vault MP4 path.

Claude Code and Codex CLI live structured adapter smoke tests passed on 2026-08-28.
