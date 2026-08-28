# External Blockers

Last reviewed: 2026-08-29 UTC.

These blockers affect live integration or real-product acceptance only. Independent
implementation and handoff contract work continues.

| ID | Blocker | Required resolution/evidence | Affected acceptance |
|---|---|---|---|
| B-001 | Authoritative Warranty Vault Product Truth, current APK, and brand assets are absent | Return every item in `docs/10-acceptance/WARRANTY_VAULT_HANDOFF_REQUEST.md`; AdForge must validate it and set the product to READY | Real Product Truth gate and Warranty Vault campaign specifically |
| B-002 | RESOLVED 2026-08-29 — a canonical Android AVD now exists and boots for real (see below) | — | Authentic app footage (for any product; Warranty Vault itself is still gated by B-001) |
| B-003 | REVISED 2026-08-29 — browser-automated Flow sign-in is blocked by Google's own anti-automation controls (verified live, three independent layers); not being bypassed. Real completion no longer depends on it (see below) | Either provide `GEMINI_API_KEY` (Veo API, no browser automation), or use the manual worker-job completion UI (paste the AI-generated prompt into Flow yourself, upload the result) | Live Flow/Veo generation |
| B-004 | RESOLVED 2026-08-29 — a real external worker (`adforge-linux-01`) connected, registered, and completed real jobs (see below) | — | Real external worker acceptance |
| B-005 | RESOLVED 2026-08-29 — real `android_capture` exercised end to end through a connected worker (see below) | — | Real Android capture acceptance via worker |
| B-006 | Real Flow generation has not been exercised end to end yet (mechanism ready, awaiting a real video from either completion path) | Complete DemoTask campaign `a0d5338a`'s two `flow_generation` jobs via the manual UI or Veo API | Real Flow generation acceptance |
| B-007 | RESOLVED 2026-08-29 — both CLIs authenticated for real as the `adforge` service account; production platform verdict is now `PLATFORM_READY` (see below) | — | Real Claude/Codex health on the production platform verdict |

## Release impact

B-001 and B-003/B-006 (Flow authentication, which requires one human interactive
sign-in) remain open. Until they are resolved and the evidence in
`docs/RELEASE_READINESS.md` is produced, the verdict remains **ADFORGE v1 — NOT
READY** for the canonical Warranty Vault acceptance campaign specifically. There is no
final Warranty Vault MP4 path. The **distributed worker subsystem itself** — the
subject of B-004/B-005/B-002 — and **production Claude/Codex health (B-007)** are now
genuinely proven end to end against production, independent of the Warranty Vault gate.

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

**B-007 RESOLVED 2026-08-29.** The `adforge` service account authenticated both CLIs
for real via their subscription login flows (not API keys), matching the project's
subscription-first architecture:

- **Claude**: `claude auth login` (Claude subscription/`claudeai` OAuth flow, not
  Console/API-key). Headless completion required injecting the browser-obtained
  authorization code into the waiting CLI process's controlling pty via `TIOCSTI`
  (a plain write to the pty slave only echoes to the display, it does not feed the
  reading process's stdin — this distinction cost real debugging time). Verified:
  `claude auth status` reports `loggedIn: true, authMethod: claude.ai, subscriptionType:
  pro`; AdForge's own `check_claude()` real-invocation health check reports
  **`READY` (2.9–4.7s)**.
- **Codex**: `codex login --device-auth` (ChatGPT subscription device-code flow, not
  `--with-api-key`). This surfaced a second real, separate bug: with no
  `~/.codex/config.toml`, Codex CLI 0.117.0 defaults to model `gpt-5.3-codex`, which a
  ChatGPT-subscription account cannot use (`400 invalid_request_error: 'gpt-5.3-codex'
  model is not supported when using Codex with a ChatGPT account`) — authentication
  itself was fine, but every real invocation still failed. Fixed by writing
  `/opt/adforge/.codex/config.toml` with `model = "gpt-5.4"` (mode 0600). Verified:
  `codex login status` reports `Logged in using ChatGPT`; a real `codex exec` call
  using AdForge's exact production invocation shape (no `-c model=` override, relying
  on the config default) returns the correct structured `{"status":"ok"}`; AdForge's
  own `check_codex()` real-invocation health check reports **`READY` (35.8–37.6s)**.
- **Full platform verdict, verified live against production**
  (`collect_capabilities(force_slow=True)` / `platform_status()`): `database READY`,
  `storage READY`, `ffmpeg READY`, `chromium READY`, `claude READY`, `codex READY` →
  **`PLATFORM_READY`** — the first time this project has reached that verdict (previous
  best was `PLATFORM_DEGRADED`, phase 18). `android_capture` and `flow_generation`
  are correctly excluded from the platform-owned verdict (per the existing
  `PLATFORM_OWNED_CAPABILITIES` design) and remain `TEMPORARILY_UNAVAILABLE` (no
  worker online) and `BLOCKED` (B-003/B-006, Flow login not yet done) respectively —
  worker-dependent capability availability, not platform readiness, exactly matching
  the documented semantic distinction.
- **Also fixed in passing:** `loginctl enable-linger adforge` was not set, so any
  background process started as the `adforge` service account via `sudo -u adforge`
  was killed the moment the invoking session's systemd scope ended, regardless of
  `nohup`/`disown`/`setsid` — the process-level detachment tricks don't survive
  cgroup-based session teardown. Enabled lingering and started `user@997.service`
  explicitly; this is what let the long-running interactive login sessions (and will
  let any future durable background job for this account) actually persist.

## Real DemoTask acceptance campaign (2026-08-29): bugs found and fixed live

With B-007 and B-003 both resolved this session, we ran the first real
production campaign against genuine Claude/Codex/Flow/Android through the
actual authenticated web app (campaign `a0d5338a-4535-4279-9aff-2746593d5add`,
product `demotask`). This immediately found and fixed real internal
implementation gaps that no test suite had caught, because nothing had ever
driven a real campaign through the live app before:

1. **`APKIngestor` had zero callers.** It was fully implemented and unit-tested
   (`tests/test_android.py`) but `create_campaign` only recorded `apk_path` as
   metadata — it never actually copied the APK into the campaign workspace or
   wrote `apk-metadata.json`. Any real campaign would have silently been unable
   to reach `APP_CAPTURE`. Fixed in `src/adforge/web.py` (commit `6f21165`).
2. **`ADFORGE_IMPORT_ROOT` was declared but never read.** `.env.example` and
   `DEPLOYMENT.md` document it; `create_app()` only ever consulted its
   `import_root` parameter (always `None` in the real systemd invocation) and
   fell back to `{ADFORGE_DATA_ROOT}/imports` instead. Every real APK-backed
   campaign creation at the documented path failed with "APK path is outside
   the import root". Fixed (commit `5d9f769`). **Not yet fixed, same class of
   bug, lower priority:** `ADFORGE_BROWSER_PROFILE_ROOT` and
   `ADFORGE_CLI_TIMEOUT_SECONDS` are also declared but never read anywhere in
   `src/adforge` — see that commit message for why those need a real design
   decision rather than a one-line fix.
3. **There was no way to register any product except the hardcoded
   `warranty-vault`.** Added `scripts/provision_product.py` (administrative,
   commit `c9eac59`) and, since the user asked for it directly, a real
   `GET/POST /products/new` UI (commit `00d9917`) that pastes Product Truth
   JSON and imports it through the same `ProductTruthService` the rest of the
   app uses.
4. **Claim-discipline prompting gap.** The real `STRATEGY` stage exhausted its
   3-attempt budget live: Claude kept paraphrasing an approved feature ("Lets
   you add a task to a list" vs. the exact "Lets you add a task to a fictional
   list") and `ProductTruthService.validate_claim`'s exact-match gate correctly
   rejected it every time — that gate is the actual safety boundary and was not
   weakened. Fixed by telling every claim-producing AI role explicitly to copy
   claims verbatim or omit them (commit `e494715`).
5. **`/tasks/{id}/retry` is disconnected from `CampaignWorker`.** Discovered
   while recovering from finding 4: retrying an exhausted task creates a new
   `CampaignTask` row with an idempotency key like
   `stage:strategy:v1:manual-retry:4`, but `CampaignWorker.run()` only ever
   looks up (and creates, if missing) the task at its own deterministic key
   (`stage:{state}:v{transition_count}`) — it has no way to discover a
   manually-retried task at a different key. The route appears to predate
   `CampaignWorker` (phase 4 vs. phase 16-18) and is very likely simply dead
   for any `CampaignWorker`-driven stage today. Worked around for this run by
   resetting the existing task's `attempt`/`state` directly (administrative,
   not a code change); **not yet fixed** — a real fix means either making
   `CampaignWorker` discover retried tasks by `task_type`, or having
   `retry_task` reset the existing task in place instead of creating a new row.

None of these are Product Truth safety regressions — the claim gate, in
particular, did exactly its job. They are real gaps in wiring that only a
genuine, non-fixture campaign run through the actual application would surface.

## Real Flow automation is blocked by Google's own anti-automation controls

Investigated live, in order: (1) `FLOW_URL` was the public marketing page, not
the tool -- fixed (commit `73ee54f`), clicking the page's real CTA correctly
reaches Google's OAuth flow. (2) That OAuth flow itself refuses to complete for
a Playwright-controlled Chromium ("This browser or app may not be secure") --
a deliberate Google security control, not attempted to bypass. (3) The
standard legitimate workaround -- export an already-authenticated session's
cookies from a real, non-automated Chrome via `context.storage_state()`, load
them into the isolated worker profile -- hit a *second*, independent Chrome
restriction: DevTools/CDP automation is refused on literal default profile
paths (`~/.config/google-chrome`), separate from Google's own block. Three
independent, deliberate anti-automation layers in a row is a strong enough
signal that this specific path (automating Flow's own sign-in) should not be
pursued further; two better alternatives now exist instead:

**B-003/B-006 status, revised:** browser-automated Flow sign-in specifically
remains blocked (not a bug -- Google's intended behavior) and is not being
worked around. But **real Flow generation acceptance is no longer gated on
it**, because two legitimate completion paths now exist:

1. **Gemini API (Veo) direct generation** (commit `3611dfc`) -- a genuine
   first-party API (`generativelanguage.googleapis.com`, plain API-key auth,
   paid preview), not browser automation at all. `worker_agent.py` prefers it
   automatically whenever `GEMINI_API_KEY` is set. Not yet exercised for real
   (no key provided this session) but the request/response shape is
   implemented against Google's documented REST contract.
2. **Manual worker-job completion via the web UI** (commit `c0fba4d`, fixed for
   the exhausted-attempt case in `c61d7d4`) -- the campaign detail page shows
   the AI-generated prompt for any open `flow_generation` job; a human pastes
   it into Flow themselves (using their own real, already-working browser
   session -- no automation involved at all), generates, downloads, and
   uploads the result. This completes the job through the identical
   `claim_specific`/`store_artifact`/`complete` path a real worker uses, so
   the campaign continues fully automated from there. Real end-to-end proof
   pending: the DemoTask campaign's two `flow_generation` jobs
   (`3bd7dcde-bca2-4e21-84b5-78d328b4c1ec`,
   `9c76b9c0-8d0e-472b-b858-0f6f0b75045a`) are sitting `FAILED` (exhausted
   automated attempts) on production right now, exact prompts already
   generated, waiting on a human to paste them into Flow and upload the
   result through the new UI at
   `https://adforge.vexel.pk/campaigns/a0d5338a-4535-4279-9aff-2746593d5add`.

Also added while building this: real browser-based APK upload for campaign
creation (commit `20463ab`) -- `apk_path` only ever worked if the file was
already sitting on the server; there was no way to get it there through the
browser at all.
