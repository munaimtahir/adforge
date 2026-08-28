# External Worker Setup (cross-platform)

The distributed worker protocol (`scripts/worker_agent.py`) is deliberately
cross-platform: a single stdlib + `httpx` script with no dependency on the rest of
the `adforge` package. It runs the same way on Windows, macOS, or Linux — only the
paths and shell syntax differ. `docs/09-operations/WINDOWS_WORKER_SETUP.md` is kept
as historical evidence from when a Windows machine was the only one available; this
document is the canonical, OS-agnostic reference. `adforge-linux-01` (a real Linux
machine) is, as of 2026-08-29, the first worker to actually complete real acceptance
end to end — see `docs/BLOCKERS.md` (B-004/B-005) and
`docs/TEST_AND_RUNTIME_EVIDENCE.md`.

## 1. Prerequisites on the worker machine

- Python 3.9+ and `pip install httpx`.
- For `android_capture`: an Android SDK with `platform-tools` and `emulator`, plus
  `cmdline-tools` (for `sdkmanager`/`avdmanager`) if the canonical AVD doesn't exist
  yet. `ANDROID_HOME`/`ANDROID_SDK_ROOT`, or the platform-default install location
  (`~/Android/Sdk` on Linux, `~/Library/Android/sdk` on macOS,
  `%LOCALAPPDATA%\Android\Sdk` on Windows), are auto-discovered. `/dev/kvm` (Linux) or
  an equivalent hardware-acceleration backend is required for the emulator to run at
  usable speed — check group membership (commonly the `kvm` group) grants read/write
  access.
- For `flow_generation`: Google Chrome (or Chromium) and
  `pip install playwright && playwright install chromium`.

## 2. Copy the agent and configure

Copy `scripts/worker_agent.py` to the worker machine, then:

```bash
python3 scripts/worker_agent.py configure --url https://adforge.vexel.pk --token <TOKEN> --name <worker-name>
```

Issue `<TOKEN>` administratively (Settings → Workers in the web UI, or the same
`issue_token()` call run server-side) — it is shown once and is not recoverable; if
lost, rotate it from Settings → Workers → that worker → Rotate credential. This
writes `~/.adforge-worker/config.json` (`%USERPROFILE%\.adforge-worker\config.json`
on Windows), mode `0600`.

## 3. Check capabilities, create the canonical AVD, start

```bash
python3 scripts/worker_agent.py doctor       # read-only: reports what's found
python3 scripts/worker_agent.py ensure-avd   # one-time, non-interactive AVD creation
python3 scripts/worker_agent.py flow-login   # one-time, only if using flow_generation
python3 scripts/worker_agent.py start        # runs indefinitely: heartbeat, claim, execute
```

`ensure-avd` accepts SDK licenses, installs
`system-images;android-36;google_apis;x86_64` if missing, creates `AdForge_API_36`,
and (fixed 2026-08-29) explicitly patches the AVD's `hw.lcd.width`/`hw.lcd.height` to
the documented `1080x1920` — `avdmanager create avd --device pixel_6` alone pulls the
device profile's native `1080x2400` skin, which previously left `device.json`
claiming a resolution the emulator wasn't actually booting at. If any step needs
something unattended tooling can't do, it reports `EXTERNAL_ACTION_REQUIRED` with the
exact reason instead of retrying blindly.

`flow-login` opens a real, visible browser at the Flow site for one-time interactive
sign-in; the persistent profile then carries that session for future headless runs.
Never commit, copy, or upload that profile directory.

Confirm the worker goes `ONLINE` at Settings → Workers on the production URL.

## Acceptance: campaigns dispatch real WorkerJobs automatically

As of Phase 18 (`src/adforge/worker_stages.py`), a campaign reaching `APP_CAPTURE` or
`ASSET_GENERATION` automatically creates a durable `android_capture` /
`flow_generation` `WorkerJob` — no manual job creation is needed. Precedence:

1. `ADFORGE_MANUAL_HANDOFF_STAGES` explicitly names the stage → the legacy manual
   `HandoffPackage` path is used instead (opt-in only, never a silent default).
2. Otherwise, a `WorkerJob` is always created. If no compatible worker is online yet,
   the campaign waits in `WAITING_FOR_WORKER` (not failed) until one connects and
   claims it.

When the job completes, its artifacts become real `Asset` records and the campaign
worker automatically continues (`WorkerJobService.on_campaign_resumed`). See
`docs/02-architecture/WORKER_PROTOCOL.md` for the exact payload/artifact contracts,
and `tests/test_worker_stages.py` for the full behavioral proof (correct payloads,
claim matching, no-worker-online waiting, artifact import + auto-resume, duplicate
completion safety, retry/failure handling, restart persistence, manual fallback).
