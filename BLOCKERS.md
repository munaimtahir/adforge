# External Blockers

Last reviewed: 2026-08-28 UTC.

These blockers affect live integration or real-product acceptance only. Independent
implementation and handoff contract work continues.

| ID | Blocker | Required resolution/evidence | Affected acceptance |
|---|---|---|---|
| B-001 | Authoritative Warranty Vault Product Truth, current APK, and brand assets are absent | Supply the handoff defined by `prompts/PRODUCT_TRUTH_HANDOFF_PROMPT.md`, including claim evidence and APK checksum | Real Product Truth gate and Warranty Vault campaign |
| B-002 | No Android AVD or connected device is configured | Configure one canonical emulator/device and prove install, reset, launch, capture, and pull against the supplied APK | Authentic app footage |
| B-003 | Chromium is not installed and Flow login state is unavailable | Install supported Chromium/Playwright runtime and authenticate a persistent profile without committing it | Live Flow/Veo generation |
| B-004 | FFmpeg and ffprobe are not installed | Install both binaries and pass deterministic render plus ffprobe gates | Draft/final render acceptance |

Claude Code and Codex CLI live structured adapter smoke tests passed on 2026-08-28.
