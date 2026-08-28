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
