# Environment Capabilities

Inventory date: 2026-08-28 UTC. Re-run `python3 scripts/environment_doctor.py`
for current, machine-readable results. Probes intentionally do not print account
identity, tokens, cookies, or browser-profile content.

| Capability | Detected | Phase impact |
|---|---:|---|
| Linux VM / Python 3.12 / Git | Yes | Core development supported |
| Node.js 25 / npm 11 | Yes | Optional frontend tooling supported |
| Codex CLI 0.150.1 | Yes, authenticated | Real structured adapter smoke passed in 8.125s |
| Claude Code 2.1.248 | Yes, authenticated | Real structured adapter smoke passed in 5.619s |
| Android SDK, ADB, emulator binary | Yes | Adapter commands can be exercised |
| Android virtual device | No configured AVD | Real emulator capture is externally blocked |
| Connected ADB device | None | Real device capture is unavailable |
| FFmpeg / ffprobe | No | Canonical render integration is blocked until installed |
| Chromium | No | Live Flow browser automation is blocked |
| Caddy 2.11.4 / systemd | Yes | Deployment configuration can be validated |

Host storage had approximately 28 GiB free (81% used) at discovery. AdForge must
report pressure and must never auto-prune campaign data.

## Runtime directories

Production uses the `/opt/adforge` contract. Development uses the ignored
`.adforge-runtime/` root. Secret-bearing and browser-profile directories are mode
`0700`; generated media and runtime databases remain outside Git.

## Graceful degradation

Provider, browser, emulator, and render health are capabilities rather than startup
requirements. Their absence must produce actionable health results and durable
`BLOCKED` or `WAITING_FOR_EXTERNAL_ASSET` state while handoff paths remain usable.

## Live provider evidence

On 2026-08-28 UTC, the production `ClaudeCodeProvider` and `CodexCLIProvider`
executed the same bounded, no-tool request against their authenticated subscription
CLIs. Both returned `{"status":"ok"}` conforming to the supplied JSON Schema. This
proves CLI invocation, authentication, structured output, and adapter parsing; it
does not claim that unrelated creative tasks or external media providers were tested.
