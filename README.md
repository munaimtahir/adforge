# AdForge AI Dev Pack v1

**Status:** LOCKED DEVELOPMENT BASELINE  
**Acceptance product:** Warranty Vault  
**Deployment:** Single Linux VM / single user / desktop-first web UI  
**Definition of done:** A user enters a campaign brief in AdForge and receives a QC-checked, publish-ready MP4 without manual external editing.

## Canonical principle

> **AI reasons. AdForge orchestrates. Specialized tools execute. QC verifies. Campaign state persists.**

## Start here

1. Read `docs/00-governance/LOCKED_DECISIONS.md`.
2. Read `docs/01-product/PRODUCT_REQUIREMENTS.md`.
3. Read `docs/02-architecture/SYSTEM_ARCHITECTURE.md`.
4. Read `docs/10-acceptance/DEFINITION_OF_DONE.md`.
5. Execute `prompts/MASTER_SINGLE_SPRINT_BUILD_PROMPT.md`.

The build prompt is intentionally multi-phase but is one continuous autonomous sprint. A phase cannot be passed until its mandatory gates pass. Ordinary failures are repaired without waiting for user input. Only genuinely user-dependent blockers may be skipped/recorded or escalated according to the locked failure policy.

## v1 production path

Brief → Product Truth → Strategy → Script → Storyboard → Asset Plan → AI Media → Android Capture → Audio → Edit Plan → Draft → QC → Repair → Final Render → Export

## Implemented application

AdForge is an installable Python 3.12/FastAPI application with SQLite persistence,
server-rendered desktop UI, durable stage worker, Product Truth enforcement,
Claude/Codex subscription CLI adapters, Flow and emulator handoffs, audio production,
FFmpeg rendering, QC/repair, backups, and deployment artifacts.

Development startup:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
export ADFORGE_DATA_ROOT="$PWD/.adforge-runtime"
export ADFORGE_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
.venv/bin/python -m adforge.auth
# Put the emitted hash in ADFORGE_ADMIN_PASSWORD_HASH, then:
.venv/bin/uvicorn adforge.web:create_app --factory --host 127.0.0.1 --port 8080
```

Production deployment is documented in `docs/09-operations/DEPLOYMENT.md`. Warranty
Vault acceptance is intentionally blocked until the authoritative handoff in
`docs/10-acceptance/WARRANTY_VAULT_HANDOFF_REQUEST.md` is supplied and validated.
