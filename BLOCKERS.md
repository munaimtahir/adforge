# External Blockers

Last reviewed: 2026-08-29 UTC.

These blockers affect live integration or real-product acceptance only. Independent
implementation and handoff contract work continues.

| ID | Blocker | Required resolution/evidence | Affected acceptance |
|---|---|---|---|
| B-001 | Authoritative Warranty Vault Product Truth, current APK, and brand assets are absent | Return every item in `docs/10-acceptance/WARRANTY_VAULT_HANDOFF_REQUEST.md`; AdForge must validate it and set the product to READY | Real Product Truth gate and Warranty Vault campaign specifically |
| B-002 | RESOLVED 2026-08-29 — a canonical Android AVD now exists and boots for real (see below) | — | Authentic app footage (for any product; Warranty Vault itself is still gated by B-001) |
| B-003 | Chrome/Playwright run, but the persistent profile lacks proven Flow generation access | Authenticate a Flow-capable subscription in the mode-0700 profile and pass a real generate/download/import smoke test | Live Flow/Veo generation |
| B-004 | RESOLVED 2026-08-29 — a real external worker (`adforge-linux-01`) connected, registered, and completed real jobs (see below) | — | Real external worker acceptance |
| B-005 | RESOLVED 2026-08-29 — real `android_capture` exercised end to end through a connected worker (see below) | — | Real Android capture acceptance via worker |
| B-006 | Real Flow generation has not been exercised through a distributed worker | With B-003 resolved, run a `flow_generation` job through a connected worker end to end (generate, download, upload) | Real Flow generation acceptance via worker |
| B-007 | PARTIALLY RESOLVED 2026-08-29 — installation/PATH fixed for both CLIs on the `adforge` service account; only interactive authentication remains (see below) | Run `claude login` / `codex login` (or provide subscription/API-key credentials) as the `adforge` user — the one remaining human action | Real Claude/Codex health on the production platform verdict |

## Release impact

B-001 and B-003/B-006 (Flow authentication, which requires one human interactive
sign-in) remain open. Until they are resolved and the evidence in
`docs/RELEASE_READINESS.md` is produced, the verdict remains **ADFORGE v1 — NOT
READY** for the canonical Warranty Vault acceptance campaign specifically. There is no
final Warranty Vault MP4 path. The **distributed worker subsystem itself** — the
subject of B-004/B-005/B-002 — is now genuinely proven end to end against production,
independent of the Warranty Vault gate.

## Distributed worker status (2026-08-29)

Phase 18 (`src/adforge/worker_stages.py`, deployed commit `4e98ec4`) wires
`CampaignWorker` into the live application for the first time and adds real stage
handlers: `APP_CAPTURE` and `ASSET_GENERATION` now automatically create durable
`android_capture`/`flow_generation` `WorkerJob`s instead of only supporting the old
manual `HandoffPackage` handoff (which remains available as an explicit, opt-in
fallback via `ADFORGE_MANUAL_HANDOFF_STAGES`, never a silent default). See
`docs/09-operations/EXTERNAL_WORKER_SETUP.md` and `tests/test_worker_stages.py`.

**B-004 REAL EXTERNAL WORKER ACCEPTANCE: READY.** A real second machine
(`adforge-linux-01`, Linux x86_64, not this VM) registered against
`https://adforge.vexel.pk` over the real public internet, credential issued
administratively, and went `ONLINE`. It proved the full protocol twice: once via
`synthetic_echo` (job `798b8150-7260-4e0f-8ede-d6c279f72740`) and once by claiming a
job left `PENDING` while genuinely offline, then completing it after reconnecting
(job `22f3c205-9e17-45ac-8b51-482651697b71`, campaign
`7604c7f6-9244-45f0-9c03-34bb8676d469`) — proving `WAITING_FOR_WORKER` doesn't fail a
campaign and that reconnection auto-resumes it. Unlike the earlier `public-https-probe-vm`
proof (credential revoked immediately after, since that probe ran on this same VM),
this credential is the real, standing worker identity and remains active.

**B-005 REAL ANDROID CAPTURE ACCEPTANCE: READY.** Campaign
`7d26cb2e-8474-4adc-a3b9-1a96f3d0d133` (product "DemoTask", a fictional productivity
app authored and compiled locally from source for this test — a real signed APK, not
a mock) went `APP_CAPTURE` → real `android_capture` `WorkerJob`
(`079749fc-bc9d-4f37-a1f1-4e3195854ae3`) → claimed and executed by
`adforge-linux-01` → real canonical-AVD install/launch/screenshot/screen-recording →
6 checksum-verified artifacts uploaded → `Asset` records imported → campaign
auto-resumed and advanced past `APP_CAPTURE` (blocking only at the next stage, which
has no handler yet — expected). The server independently re-validated the recording
with `ffprobe`: real H.264, **1080×1920** (see the resolution fix below), 3.4s
duration. Lease-crash recovery was also proven for real: a job claimed then abandoned
(simulating a worker crash) correctly reclaimed to `PENDING` after lease expiry
(`DEFAULT_LEASE_SECONDS`), was completed on a second attempt with no duplicate
artifacts or completion, and the full `WorkerJobAttempt` history (`CLAIMED` →
`CLAIMED` → `COMPLETE`) was preserved.

**Canonical AVD resolution bug found and fixed.** `ensure_canonical_avd()` declared
`CANONICAL_RESOLUTION = "1080x1920"` but never applied it —
`avdmanager create avd --device pixel_6` pulls the device profile's native skin
(`1080x2400`), so the emulator silently booted at the wrong resolution while
`device.json` kept claiming `1080x1920`. Fixed in `scripts/worker_agent.py`
(`apply_canonical_display_config`); verified for real (`wm size` before/after).

**Design gap noted, not yet fixed:** the production control plane has no Android SDK
at all (by design — Android tooling is worker-only), so `APKIngestor` can never
actually resolve an APK's `package_id` server-side (`inspection_status` is always
`AAPT_UNAVAILABLE`), yet `build_app_capture_handler`'s `WorkerJob` payload requires
`package_id`. For this acceptance run, `package_id` was supplied out-of-band (the same
role a human operator would play submitting the APK) rather than resolved from
inspection. A real fix likely means either accepting `package_id` as explicit campaign
input, or having the worker (which does have `aapt`) resolve it from the downloaded
APK before install.

**Production bug found and fixed in passing:** a campaign from an earlier session's
probe (`79eccabd-631d-4baa-93ea-cff50764d84d`, "Public probe") was left with
`active=True` and never released, which — because only one campaign may hold the
active lease system-wide — silently blocked every subsequent `resume()` (including
the first attempt at this session's own `synthetic_echo` proof, which hit a 500).
Released via the existing `Orchestrator.release_lease()`; worth a periodic-sweep or
stale-lease-alert follow-up so this can't recur silently.

## B-007 status (2026-08-29)

Diagnosed for real as the `adforge` service account (not `munaim`), with the correct
`HOME`/cwd (an earlier check was misleading: codex resolves "project config" relative
to the current working directory, not just `$HOME`, so an invocation from the wrong
cwd reads a different user's config and reports a spurious permission error).

- **Claude**: `claude` was not on the `adforge` account's PATH at all (only under a
  personal `nvm` install). Fixed: `sudo npm install -g @anthropic-ai/claude-code`
  (system-wide, `/usr/local/bin`, already on the service account's PATH — no
  copying of `munaim`'s private credentials). `claude --version` and a real
  `-p`/non-interactive invocation both now work; the invocation cleanly reports
  `Not logged in · Please run /login` — **CLAUDE_AUTHENTICATION_REQUIRED**, not a
  PATH/installation problem anymore.
- **Codex**: was already on PATH (pre-existing). A real `codex exec` invocation
  reproduces the documented `401 Unauthorized` against
  `wss://api.openai.com/v1/responses` cleanly — **CODEX_AUTHENTICATION_REQUIRED**.
- Both CLIs support headless, non-interactive authentication as an alternative to
  subscription login: Claude Code's `--bare` mode uses `ANTHROPIC_API_KEY` (or
  `apiKeyHelper`) exclusively; Codex supports
  `printenv OPENAI_API_KEY | codex login --with-api-key`. This has different billing
  implications (pay-per-token API vs. subscription) — an operator decision, not made
  here.
- Production's own real-invocation health check now reflects this:
  `claude: BLOCKED | CLAUDE_AUTHENTICATION_REQUIRED` (previously `NOT_READY`, since it
  wasn't even resolvable) and the **overall platform verdict improved from
  `PLATFORM_NOT_READY` to `PLATFORM_DEGRADED`** — verified live against production.

**The exact one remaining human action for B-007:** run, as the `adforge` service
account on the production VM (`sudo -u adforge -H bash -lc 'cd /opt/adforge && claude
login'` and the equivalent `codex login`), either the interactive subscription login
or provide API keys for headless auth — whichever billing model is preferred.
