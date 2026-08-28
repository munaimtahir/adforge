# Windows Worker Setup (B-004/B-005/B-006)

This closes the one remaining piece of distributed-worker acceptance that cannot be
automated from the VM: a real external machine actually connecting. Everything else
(server, protocol, worker agent code) is already implemented and deployed.

A worker credential for `adforge-windows-01` already exists in production (issued
2026-08-28 via the same `issue_token()` code path the Settings → Workers UI uses). The
raw token was shown once in that session and is not recoverable — if it was lost, issue
a new one from Settings → Workers → this worker → Rotate credential, or the same
server-side script pattern.

## 1. Prerequisites on the Windows laptop

- Python 3.12+ (`python --version`)
- `pip install httpx`
- For Android capture: Android Studio or the standalone SDK command-line tools,
  with at least the `platform-tools` and `emulator` packages, and either
  `ANDROID_HOME` or `ANDROID_SDK_ROOT` set (the agent also searches
  `%LOCALAPPDATA%\Android\Sdk`, the default Android Studio location).
- For Flow generation: Google Chrome, and `pip install playwright && playwright
  install chromium`.

## 2. Copy the agent

Copy `scripts/worker_agent.py` from this repository to the laptop. It has no
dependency on the rest of the `adforge` package — only `httpx` (and `playwright`,
optionally, for Flow).

## 3. Configure

```powershell
python worker_agent.py configure --url https://adforge.vexel.pk --token <TOKEN> --name adforge-windows-01
```

Replace `<TOKEN>` with the credential issued for `adforge-windows-01`. This writes
`%USERPROFILE%\.adforge-worker\config.json`, restricted to the current user.

## 4. Check capabilities

```powershell
python worker_agent.py doctor
```

Reports exactly what was found: Android SDK root, `adb`/`emulator` paths, whether the
canonical AVD (`AdForge_API_36`) exists, Chrome path, whether Playwright is installed,
and whether the Flow profile is configured. Nothing here modifies anything.

## 5. Create the canonical Android Virtual Device (one-time, if not already present)

```powershell
python worker_agent.py ensure-avd
```

Best-effort and non-interactive: accepts SDK licenses, installs the
`system-images;android-36;google_apis;x86_64` package if missing, and creates
`AdForge_API_36`. If any step needs something the tooling can't do unattended (e.g. a
missing `cmdline-tools` install), it reports `EXTERNAL_ACTION_REQUIRED` with the exact
reason rather than retrying blindly — install/update via Android Studio's SDK Manager
and re-run.

## 6. Authenticate Flow (one-time, only if using flow_generation)

```powershell
python worker_agent.py flow-login
```

Opens a real, visible Chrome window at the Flow site. Sign in, wait for the page to be
ready, then press Enter in the terminal. The persistent profile
(`%USERPROFILE%\.adforge-worker\flow-profile`) then carries that session for future
headless runs. Never commit, copy, or upload this profile directory — it is
per-machine only and is *not* something the worker ever sends to the VM.

## 7. Start the worker

```powershell
python worker_agent.py start
```

Runs indefinitely: heartbeats every 30s, polls for a matching job every 5s, executes
whatever it claims, uploads artifacts, and reports completion or a classified failure.
Outbound HTTPS only — no inbound port is ever opened. Confirm it went `ONLINE` at
Settings → Workers on `https://adforge.vexel.pk`.

## Acceptance commands (run once the worker is `start`ed and online)

There is currently no UI button to create a one-off `android_capture`/
`flow_generation` job — a campaign reaching the relevant stage will create one once
that orchestrator wiring lands (not yet done; see `docs/IMPLEMENTATION_STATUS.md`
Phase 17 notes). Until then, real acceptance can be proven directly the same way the
public `synthetic_echo` proof was run: create a `WorkerJob` with the appropriate
capability and payload against the production database, let the connected Windows
worker claim and execute it, and inspect the resulting `WorkerArtifact` rows and
uploaded files. See `docs/02-architecture/WORKER_PROTOCOL.md` for the exact payload
contracts.
