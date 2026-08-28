# External Blockers

Last reviewed: 2026-08-28 UTC.

These blockers affect live integration or real-product acceptance only. Independent
implementation and handoff contract work continues.

| ID | Blocker | Required resolution/evidence | Affected acceptance |
|---|---|---|---|
| B-001 | Authoritative Warranty Vault Product Truth, current APK, and brand assets are absent | Return every item in `docs/10-acceptance/WARRANTY_VAULT_HANDOFF_REQUEST.md`; AdForge must validate it and set the product to READY | Real Product Truth gate and Warranty Vault campaign |
| B-002 | No Android AVD or connected device is configured | Configure one canonical emulator/device and prove install, reset, launch, capture, and pull against the supplied APK | Authentic app footage |
| B-003 | Chrome/Playwright run, but the persistent profile lacks proven Flow generation access | Authenticate a Flow-capable subscription in the mode-0700 profile and pass a real generate/download/import smoke test | Live Flow/Veo generation |
| B-004 | No external worker machine (e.g. a Windows laptop) has connected to the distributed worker API | Run `scripts/worker_agent.py configure` + `start` from a real second machine against `https://adforge.vexel.pk` and observe it register, heartbeat, and go ONLINE in Settings → Workers | Real Windows worker acceptance |
| B-005 | Real Android capture has not been exercised through a distributed worker | With B-002 and B-004 resolved, run an `android_capture` job through a connected worker end to end (install, launch, screenshot, record, upload) | Real Android capture acceptance via worker |
| B-006 | Real Flow generation has not been exercised through a distributed worker | With B-003 and B-004 resolved, run a `flow_generation` job through a connected worker end to end (generate, download, upload) | Real Flow generation acceptance via worker |
| B-007 | The production `adforge` service account cannot run Claude Code or Codex CLI: `claude` is not on that account's PATH at all (it is only installed under a personal nvm Node install), and `codex` is on PATH but has no authenticated session for that account (`401 Unauthorized` against the OpenAI websocket endpoint) | Install Claude Code where the `adforge` account's PATH resolves it (or set an absolute path AdForge can use) and run `claude login`/`codex login` as the `adforge` user with the appropriate subscription credentials | Real Claude/Codex health on the production platform verdict; discovered 2026-08-28 by the new real-invocation health check replacing the old `shutil.which`-only check, which would have silently reported Codex "READY" on mere PATH presence |

## Release impact

All blockers are required for the canonical real-product/real-worker acceptance. Until
they are resolved and the evidence in `docs/RELEASE_READINESS.md` is produced, the
verdict remains **ADFORGE v1 — NOT READY**. There is no final Warranty Vault MP4 path.

Claude Code and Codex CLI live structured adapter smoke tests passed on 2026-08-28 in
the development environment (as user `munaim`). B-007 shows this does **not** currently
hold for the production `adforge` service account — see `docs/TEST_AND_RUNTIME_EVIDENCE.md`.

## Distributed worker status (2026-08-28)

The distributed worker foundation (Phase 16) is implemented and independently proven
with a real HTTP round trip using the `synthetic_echo` capability (register → heartbeat
→ claim → lease → artifact upload → complete → `WAITING_FOR_WORKER` campaign auto-resume),
both against a local dev server and, on this date, **against the live production endpoint
`https://adforge.vexel.pk` itself** (worker `public-https-probe-vm`, job
`e90b1c89-4182-4542-900e-a0c03d9cf317`, credential revoked immediately after the proof
run). This confirms the protocol works over the real public internet path, not just
localhost — but it is not B-004 acceptance: the probe ran on this same Linux VM, not on
an actual separate Windows machine, which remains genuinely unavailable to this
environment (confirmed absent: no SSH config, no known host, no credentials, no reachable
peer on any local network interface).

Phase 17 replaces the `android_capture`/`flow_generation` `EXTERNAL_ACTION_REQUIRED`
stubs in `scripts/worker_agent.py` with real, argument-array-only implementations
(Android SDK/AVD discovery and capture pipeline; Playwright-based Flow login/generation
pipeline reusing the same worker protocol, no new networking). Android SDK discovery and
Flow login-state detection were verified for real against this VM's actual (device-less)
SDK install and the live Flow site respectively — both correctly report the absence they
were expected to report (no canonical AVD; `LOGIN_REQUIRED`). Neither pipeline has been
exercised against real hardware/a real authenticated session, which the locked
architecture explicitly forbids attempting on this VM.

**B-004 REAL WINDOWS WORKER ACCEPTANCE: BLOCKED — external Windows machine not present
in this execution context.**
**B-005 REAL ANDROID CAPTURE ACCEPTANCE: BLOCKED — external Windows/Android worker not
present; implementation is READY.**
**B-006 REAL FLOW ACCEPTANCE: BLOCKED — requires an authenticated Flow session, which
requires interactive human sign-in (`scripts/worker_agent.py flow-login`); implementation
is READY.**
