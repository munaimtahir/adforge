# AdForge v1 Release Readiness

**ADFORGE v1 — NOT READY**

Audit date: 2026-08-28 UTC.

## Outcome

The independent application implementation is complete through all planned v1
subsystems and its automated gates are green. The release contract is not satisfied
because the real Warranty Vault acceptance campaign cannot pass the mandatory Product
Truth gate and therefore has no authentic APK capture, real Flow media, campaign audio,
edit plan, QC record, manifest/ledger export, or final 9:16 MP4.

The real UI attempt is campaign `f708b042-7310-4fb1-952e-f7882d8ad79e`. It was created
through authenticated HTTP, selected Warranty Vault, stored the canonical brief, and
correctly received HTTP 409 on start because readiness is `UNKNOWN`. Evidence is under
`docs/10-acceptance/evidence/f708b042-7310-4fb1-952e-f7882d8ad79e/`.

## Independent implementation evidence

- 83 automated tests pass; Ruff and strict mypy pass.
- Real authenticated Claude Code and Codex adapter smoke checks passed.
- Real FFmpeg/ffprobe rendering and media QC pass for deterministic fixtures.
- Flow Chrome automation reaches the service, but generation controls report
  `LOGIN_REQUIRED`; no generation success is claimed.
- ADB and emulator binaries are installed, but there is no AVD or connected device.
- Generation and emulator handoff round trips validate checksums/filenames and resume
  fixture campaigns; these are contract evidence, not Warranty Vault acceptance.
- Caddy configuration and systemd unit validate.
- Tracked-file secret scan and schema/config checks pass.

## Exact evidence still required for release

1. Complete and validate `WARRANTY_VAULT_HANDOFF_REQUEST.md`; set truth to `READY`.
2. Ingest the supplied current APK and record matching package/version/SHA-256.
3. Authenticate a Flow-capable persistent browser profile and prove real generation,
   download, provenance, credit/attempt ledger, and import—or execute a real external
   generation handoff against the Warranty Vault campaign.
4. Configure one canonical Android AVD/device and capture authentic Warranty Vault UI,
   or execute the real emulator handoff against the supplied APK.
5. Run the complete campaign from the web UI, including autonomous creative/audio,
   edit plan, draft, induced recoverable failure, targeted repair, controlled restart,
   QC, final render, and export.
6. Store the playable final MP4, checksum, ffprobe output, screenshot, truth snapshot,
   manifest, ledger, QC report, and render spec under the acceptance evidence contract.

Final Warranty Vault MP4 path: **not produced**.

## Addendum (2026-08-29): distributed worker subsystem now independently proven

This verdict is unchanged by, and independent of, the following: the campaign →
`WorkerJob` orchestration gap (previously undocumented — `CampaignWorker` existed but
was never wired into the live app) is now closed, a real second machine
(`adforge-linux-01`) connected and completed real `android_capture` jobs end to end
against production with server-side `ffprobe` validation, and the production Claude
CLI PATH/installation gap is fixed (moving the platform health verdict from
`PLATFORM_NOT_READY` to `PLATFORM_DEGRADED`). None of this substitutes for Warranty
Vault acceptance specifically, which still requires B-001 (Product Truth/APK/brand
assets) and B-003/B-006 (Flow authentication). See `docs/BLOCKERS.md` and
`docs/TEST_AND_RUNTIME_EVIDENCE.md` for full evidence.
