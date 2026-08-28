# Implementation Status

Last updated: 2026-08-28 UTC.

## Current phase

Phase 5 is complete. Replaceable Claude Code and Codex CLI adapters use subscription
CLIs, fixed argv/stdin prompts, structured JSON schemas, timeouts, secret filtering,
capability/health routing, three attempts, and provider execution ledger records.

## Phase ledger

| Phase | Status | Evidence |
|---|---|---|
| 0 — Discovery/environment/baseline | Complete | Environment doctor, capability report, blockers, baseline test |
| 1 — Application foundation/data model | Complete | Domain CRUD, migration, path, workspace, manifest, checksum, ledger tests |
| 2 — Product Truth | Complete | Import, schema/evidence, immutable snapshot, claim and provenance tests |
| 3 — State machine/orchestrator | Complete | Transition, retry, restart, lease, idempotency and repair tests |
| 4 — Desktop web control plane | Complete | Route, auth, CSRF, campaign, lease UX, path and secret tests |
| 5 — Claude/Codex providers | Complete | Contract, router, retry, schema, injection, redaction and live smoke evidence |
| 6–13 | Not started | Implementation pending |
| 14 — End-to-end acceptance | Blocked externally | Warranty Vault truth/APK/assets, emulator, Flow, and FFmpeg evidence unavailable |
| 15 — Release audit | Not started | Pending implementation |

## Baseline quality commands

```bash
python3 scripts/environment_doctor.py
python3 -m pytest
ruff check .
git diff --check
```

This status is intentionally conservative. No integration is considered proven by
the presence of a binary alone, and no mock is acceptance evidence.
