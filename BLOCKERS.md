# External Blockers

Last reviewed: 2026-08-28 UTC.

These blockers affect live integration or real-product acceptance only. Independent
implementation and handoff contract work continues.

| ID | Blocker | Required resolution/evidence | Affected acceptance |
|---|---|---|---|
| B-001 | Authoritative Warranty Vault Product Truth, current APK, and brand assets are absent | Return every item in `docs/10-acceptance/WARRANTY_VAULT_HANDOFF_REQUEST.md`; AdForge must validate it and set the product to READY | Real Product Truth gate and Warranty Vault campaign |
| B-002 | No Android AVD or connected device is configured | Configure one canonical emulator/device and prove install, reset, launch, capture, and pull against the supplied APK | Authentic app footage |
| B-003 | Chrome/Playwright run, but the persistent profile lacks proven Flow generation access | Authenticate a Flow-capable subscription in the mode-0700 profile and pass a real generate/download/import smoke test | Live Flow/Veo generation |
| B-004 | No external worker machine (e.g. a Windows laptop) has connected to the distributed worker API | Run `scripts/worker_agent.py configure` + `start` from a real second machine against the public AdForge URL and observe it register, heartbeat, and go ONLINE in Settings → Workers | Real Windows worker acceptance |
| B-005 | Real Android capture has not been exercised through a distributed worker | With B-002 and B-004 resolved, run an `android_capture` job through a connected worker end to end (install, launch, screenshot, record, upload) | Real Android capture acceptance via worker |
| B-006 | Real Flow generation has not been exercised through a distributed worker | With B-003 and B-004 resolved, run a `flow_generation` job through a connected worker end to end (generate, download, upload) | Real Flow generation acceptance via worker |

## Release impact

All blockers are required for the canonical real-product/real-worker acceptance. Until
they are resolved and the evidence in `docs/RELEASE_READINESS.md` is produced, the
verdict remains **ADFORGE v1 — NOT READY**. There is no final Warranty Vault MP4 path.

Claude Code and Codex CLI live structured adapter smoke tests passed on 2026-08-28.

The distributed worker foundation (Phase 16) is implemented and independently proven
with a real HTTP round trip using the `synthetic_echo` capability (register → heartbeat
→ claim → lease → artifact upload → complete → `WAITING_FOR_WORKER` campaign auto-resume)
on 2026-08-28. B-004–B-006 require a genuine second machine, which this environment does
not have.
