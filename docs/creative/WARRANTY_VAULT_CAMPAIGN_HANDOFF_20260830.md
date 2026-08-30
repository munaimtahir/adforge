# Warranty Vault install campaign — session handoff (2026-08-30)

## 1. Objective

Build a real, placement-ready 20-second vertical (9:16) install ad for the
Warranty Vault Android app, run through AdForge's full autonomous pipeline
(STRATEGY → SCRIPT → STORYBOARD → ASSET_PLAN → ASSET_GENERATION →
APP_CAPTURE → AUDIO_PRODUCTION → EDIT_PLAN → DRAFT_RENDER → QC → REPAIR →
FINAL_RENDER → EXPORT), for real Google Ads app-install placement contingent
on passing QC. Campaign objective: drive installs, ~2-week Google Ads flight
(the flight length is media-buying context, not an AdForge field — the ad's
own runtime is 20s).

- **Campaign ID:** `aa443afa-a05e-420e-92c0-94292b04e9f7`
- **Product:** `warranty-vault` on production (`adforge.vexel.pk`)
- **APK:** `products/warranty-vault/apk/android-app-release.apk`
  (`com.warrantyvault.android`, version `1.0.5`, versionCode `5`, real
  signed build, confirmed via `aapt dump badging` and the app's own Settings
  screen)
- **Truth doc:** `products/warranty-vault/truth/PRODUCT_TRUTH.md` (merged
  from two source-derived drafts, `truthmap1.md`/`truthmap2.md`, then
  re-verified claim-by-claim against a real on-device walkthrough)

## 2. Current state (as of this handoff)

Campaign is **BLOCKED** at `edit_plan`, waiting on one `android_capture`
WorkerJob (`bbc3188b-4979-4e0f-96fd-594c020bc356`) that has failed 5
consecutive real attempts, each on a **different, new** root cause — every
fix so far has been genuine and moved the failure point strictly forward
through the capture script, not in circles. The remaining problem is in the
**last** shot needing device capture (filling the Coverage tab's Warranty
Duration field, and reaching Save Product), not a repeat of anything already
fixed.

All pipeline stages before APP_CAPTURE completed successfully via real
Claude/Codex calls:

| Task | State |
|---|---|
| product_truth_validation | COMPLETE |
| strategy | COMPLETE |
| script | COMPLETE |
| storyboard | COMPLETE |
| asset_plan | COMPLETE |
| asset_generation | COMPLETE (2 flow_generation clips manually generated via Google Flow and uploaded through the AdForge web UI) |
| app_capture | blocked on the one android_capture WorkerJob above |
| audio_production | COMPLETE |
| edit_plan | BLOCKED — `StageDispatchError: shot shot-03-bridge-device needs an app capture, but none was imported` (this is just the downstream symptom of app_capture never completing) |

## 3. Code changes made and deployed this session (all committed to `main`, all real fixes with evidence)

Repo: `/media/munaim/shared1/Documents/github/adforge`. Every commit below
is pushed to `origin/main` and has been deployed:
- `scripts/worker_agent.py` changes: deployed to **this local machine only**
  (that's where the worker with Android SDK access runs — production never
  ran this file).
- `src/adforge/*.py` changes: deployed to **production**
  (`/opt/adforge/app/src/adforge/...`, `adforge.service` restarted after
  each), because that's the server-side pipeline code. Backups of each file
  were taken before overwrite (`*.bak-<timestamp>` alongside the original).

1. **`8cc4ff9`** — `worker_agent.py`: kill the emulator by process group and
   *wait* for exit before starting a new one, instead of a fire-and-forget
   `terminate()`. Found live: retries were leaving multiple emulator
   instances running simultaneously, starving each other of CPU and none
   booting in time (this was blocking an earlier DemoTask campaign, not
   Warranty Vault).
2. **`92f162e`** — `worker_agent.py`: also uninstall-and-retry on
   `INSTALL_FAILED_VERSION_DOWNGRADE`, not just the signature-mismatch case.
   Found live: the persistent AVD had a higher version code installed from
   an earlier session.
3. **`0ab0dac`** — `src/adforge/worker_stages.py` `app_capture_payload()`:
   **the biggest fix**. It used to take only the *first* storyboard shot
   with a `capture_instruction` and dispatch just that. This storyboard has
   7 capture shots meant to run as one continuous session (open app → add a
   product → set its warranty → attach a receipt → dashboard now shows 1
   product → Expiring Soon → Settings). Only shot 1's 3 actions were ever
   sent to the worker; shots 4-9 (the ones that actually add data) never
   ran, so the dashboard/expiring shots captured an empty, freshly-installed
   app. **This is the literal explanation for the "empty screens, no
   appliances or warranties added" bug you reported.** Fixed to chain every
   capture shot's actions together, in storyboard order, into one job (29
   actions instead of 3). Added a regression test
   (`tests/test_worker_stages.py::test_app_capture_chains_every_capture_shots_actions_in_one_session`).
4. **`8d7d131`** — `worker_agent.py`: the poll loop crashed outright on any
   transient network error (hit both a `ConnectTimeout` and later a
   `ReadTimeout` live in this session), silently killing the worker with
   nothing supervising it — a dispatched job then sits `PENDING` forever
   with no visible reason. Now logs and keeps polling.
5. **`7d065e9`** — `worker_agent.py`: `AndroidAction.timeout_seconds` /
   `retry_count` were parsed onto every DSL action but never actually read
   by the executor — `TAP_TEXT`/`ASSERT_VISIBLE`/`ASSERT_NOT_VISIBLE` each
   did exactly one immediate UI-dump check. Found live: a cold app launch
   was still on its splash icon when the first `ASSERT_VISIBLE` ran. Added
   `_poll_for_text`/`_poll_for_absence` helpers so these actions actually
   honor their declared timeout.
6. **`8ee553c`** — `worker_agent.py`: the recording-retry loop (up to 3
   inner attempts per WorkerJob attempt) replayed the *entire* DSL action
   list from action 0 on every retry **without resetting the app** — fine
   for the old random-monkey-taps capture, wrong for a stateful directed
   script. Found live: attempt 1 progressed deep into the app before some
   transient issue triggered a retry; attempt 2 replayed from the start
   against the already-mutated state and failed on "Get started not found"
   (already dismissed by attempt 1) — a confusing, unrelated-looking error.
   Now does `pm clear` + relaunch before every replay.
7. **`854804e`** — `src/adforge/creative_quality.py` +
   `src/adforge/campaign_stages.py` + `worker_agent.py`: added a new DSL
   action `TAP_TEXT_IF_VISIBLE` (taps if present, silently continues if
   not) plus STORYBOARD prompt guidance to use it for any screen that might
   or might not appear. Found live: Warranty Vault's first-run "Welcome to
   Vexel Warranty Vault / Get started" onboarding screen appeared once
   during my manual walkthrough but was **absent** on a later `pm
   clear`-based capture retry — its appearance is not a reliable signal of
   "first launch," so a hard `TAP_TEXT` on it is wrong.
8. **`35ce426`** — `products/warranty-vault/truth/PRODUCT_TRUTH.md`:
   documented the verified Category dropdown taxonomy (`Computer or
   Laptop`, `Electronics`, `Furniture`, `Home Appliance`, `Kitchen
   Appliance`, `Mobile Phone`, `Other`, `Personal Care`, `Tools` — there is
   no generic "Appliances" option) and the optional-onboarding note, so
   future storyboard generations for this product don't repeat these two
   mistakes.

All 145 repo tests pass after every change (`ruff check` clean, `mypy`
clean except two pre-existing, unrelated `no-any-return` warnings on lines
86/1080ish of `worker_agent.py` that predate this session).

## 4. What I directly verified on-device (not inferred)

I installed the real APK on a local Android emulator and manually walked
through onboarding → Home dashboard → Add Product (all 3 tabs: Product,
Coverage, Documents) → Product detail → Products list → Expiring Soon →
Settings, screenshotting each step. This is what the merged
`PRODUCT_TRUTH.md`'s evidence entries are sourced from, and it's how I know
the app itself is not the problem — every failure this session has been in
AdForge's own capture DSL/worker code or in the storyboard's assumptions
about exact UI layout, never a broken app feature.

## 5. Diagnostic history on the one still-failing WorkerJob (chronological, each a genuinely new failure after the previous fix)

All of these are the *same* job, `bbc3188b-4979-4e0f-96fd-594c020bc356`,
repeatedly reset (`attempt` -> 0, `status` -> `PENDING`) and re-patched
between rounds.

1. **`emulator did not report boot completion in time`** → root cause: bug
   #1 above (stray competing emulators). Fixed, redeployed, retried.
2. **`adb install failed: ... INSTALL_FAILED_VERSION_DOWNGRADE`** → root
   cause: bug #2 above. Fixed, redeployed, retried.
3. **`directed capture DSL failed: TAP_TEXT could not find element with
   text 'Add'`** → root cause: the WorkerJob only had shot-03's 3 actions
   (bug #3, not yet found at this point) — the real Warranty Vault app has
   no bare "Add" button (that failure was actually against a leftover
   DemoTask-style assumption from before the campaign was even fully
   switched over; superseded by later findings).
4. **`ASSERT_VISIBLE failed: 'Add product' not found`**, screenshot showed
   the app still on its splash icon → root cause: bug #5 (timeouts not
   honored). Fixed.
5. **`ASSERT_VISIBLE failed: 'Add product' not found` again**, this time
   `window_dump.xml` showed the app on the **Home dashboard already**
   (onboarding not shown) → root cause: I had manually inserted a *hard*
   `TAP_TEXT "Get started"` assuming onboarding always appears; it doesn't,
   reliably. Led to bug #7 (`TAP_TEXT_IF_VISIBLE`). Fixed.
6. **`TAP_TEXT could not find element with text 'Appliances'`** → root
   cause: storyboard invented a category label that doesn't exist in the
   app (real options listed in section 3, item 8). Manually patched this
   job's action (`Appliances` → `Kitchen Appliance`) and updated the truth
   doc for future runs.
7. **`TAP_TEXT could not find element with text 'Coverage'`**,
   `window_dump.xml` showed an open date-picker modal (`Select date`, `OK`,
   `Cancel`) → root cause: the storyboard's shot-04 taps "Purchase Date"
   (opens a calendar dialog) but never dismisses it before shot-05 tries to
   tap the "Coverage" tab, which is now covered by the modal. Manually
   inserted a `TAP_TEXT "OK"` right after the Purchase Date tap. Fixed for
   this run (not yet folded back into the truth doc/storyboard guidance —
   **see open item below**).
8. **`TAP_TEXT could not find element with text 'Warranty Duration'`**,
   `window_dump.xml` showed Provider/Provider Contact/Coverage
   Notes/Expiry Date only — the Warranty Duration field is below the fold
   on the Coverage tab and needs a scroll. Inserted one `SWIPE` (540,1600 →
   540,500) before the Warranty Duration tap and another before the Save
   Product tap.
9. **(Current failure)** Same error again —
   `window_dump.xml` now shows `Return Tracking`, `Return Period (days)`,
   `Return Deadline`, `Replacement Period (days)`, `Replacement Deadline`,
   `Return Address`, `Return Contact` — **the swipe overshot**: one
   1100px drag skipped straight past the Warranty Duration/Component
   warranty section into the middle of Return Tracking. Not yet fixed.

## 6. Root cause of the current, unresolved failure

The Coverage tab is a long scrollable form: Main Warranty (Provider,
Provider Contact, Coverage Notes, Start Date, Expiry Date, **Warranty
Duration**, Days/Months/Years chips, Component warranty toggle) → Return
Tracking (6 fields) → Insurance toggle → **Save Product** button. A single
fixed-distance `SWIPE` cannot reliably land on both "Warranty Duration"
(fairly high up) and "Save Product" (near the very bottom) — tuned for one,
it overshoots or undershoots the other, and I don't have a principled way to
calibrate the right distance without more trial-and-error cycles (each
cycle costs ~10-15 minutes: fresh emulator boot, `pm clear`, full APK
install, replay up to 3 inner × 3 outer attempts).

The DSL has no "scroll until this text is visible" primitive — only a
fixed-distance `SWIPE`. That's the structural gap. Two options I see, for
the planning agent to weigh:

- **(a)** Add a new DSL action (e.g. `SCROLL_UNTIL_VISIBLE` with
  `target_text`) that repeats a bounded small swipe (checking the UI dump
  after each) until the target text is found or a max-scroll-count is hit.
  This is the generically robust fix, in the same spirit as
  `TAP_TEXT_IF_VISIBLE` added this session — but it's a real schema +
  worker + STORYBOARD-prompt change that needs its own testing, not a
  one-line patch.
- **(b)** Rely on the app's own validation-driven auto-scroll: during my
  *manual* walkthrough, tapping "Save Product" *before* filling Warranty
  Duration triggered a validation error that auto-scrolled the form
  directly to the Warranty Duration field with focus. A DSL sequence that
  deliberately taps Save Product early (accepting the validation failure as
  a navigation step), then fills Warranty Duration where the app itself put
  the viewport, then taps Save Product again, sidesteps scroll-distance
  guessing entirely — but it's app-specific behavior to lean on, and TAP_TEXT
  for "Save Product" itself may also need one scroll to reach from the top
  of the Coverage tab.

## 7. Other open items (not yet folded into durable fixes)

- The "dismiss the Purchase Date picker with OK" step (diagnostic #7 above)
  was patched directly on the live WorkerJob's payload, **not** yet added
  to `PRODUCT_TRUTH.md`'s demo_workflows or to STORYBOARD prompt guidance.
  If the storyboard is regenerated from scratch, this bug will likely
  recur.
- The manual one-off patch scripts used this session
  (`patch_onboarding.py`, `patch_category.py`, `patch_date_ok.py`,
  `patch_scroll_coverage.py`, `reset_app_capture_v2.py`,
  `resume_v2_campaign.py`, `start_campaign.py`, `create_campaign.py`) are
  sitting in `/opt/adforge/app/` on production (not in git — they're
  operational scratch scripts, not part of the shipped codebase). They're
  useful references for how to reset/patch a WorkerJob directly via the
  `Services` API if that's still the fastest path, but they should probably
  be deleted from production once this campaign is unblocked, or formalized
  into `scripts/` if this kind of manual intervention turns out to be a
  recurring operational need.
- Two `flow_generation` clips (the live-action hook and the CTA end-card)
  were generated manually via Google Flow and uploaded through the AdForge
  web UI's "Worker jobs" manual-complete form — this worked exactly as
  designed and both `WorkerJob`s show `COMPLETE`. Nothing outstanding there.
- The stray `Android_15_Test` emulator that was found consuming 255% CPU
  for 78+ minutes (not started by me) was killed mid-session at your
  instruction. If you or another process needs that emulator for something
  else, it will need to be restarted separately.

## 8. Suggested next step for the planning agent

The fastest unblock is almost certainly a `SCROLL_UNTIL_VISIBLE`-style DSL
primitive (option (a) in section 6) — it's the same shape as the
`TAP_TEXT_IF_VISIBLE` addition already shipped this session, so the
groundwork (schema enum, worker executor pattern, STORYBOARD prompt
instruction, regression test) is a known template to follow. Once added,
this specific stuck WorkerJob can be re-patched to use it instead of the
blind `SWIPE`, and — more importantly — the STORYBOARD provider should be
told (via `ANDROID_DSL_INSTRUCTION` in `campaign_stages.py`, next to the
existing `TAP_TEXT_IF_VISIBLE` guidance) to prefer it over `SWIPE` whenever
targeting a field that may not be in the current viewport, so future
storyboards for any app with a long form don't hit this same wall.
