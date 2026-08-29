# AdForge Creative Quality 2.0 — Codex Audit

Audit date: 2026-08-29. Starting commit: `7711f5d`, branch `main`.

## Existing pipeline

`CampaignWorker` advances `PRODUCT_TRUTH_VALIDATION → STRATEGY → SCRIPT → STORYBOARD → ASSET_PLAN → ASSET_GENERATION → APP_CAPTURE → AUDIO_PRODUCTION → EDIT_PLAN → DRAFT_RENDER → QC → REPAIR → FINAL_RENDER → EXPORT`. Strategy, script, and storyboard use `CreativePipeline` and strict Pydantic output models. Asset planning associates storyboard scenes to asset needs. APP_CAPTURE and ASSET_GENERATION dispatch durable `WorkerJob`s through `worker_stages.py`; completed artifacts are imported and resume the campaign. EDIT_PLAN currently builds V1 `EditSpec` clips and overlays. `FFmpegRenderer` uses resolved executable paths, argument arrays, safe workspace paths, text files for drawtext, and ffprobe validation. QC combines media/truth/asset checks and hooks, while repair creates bounded targeted tasks. Manifests and ledger events are stored per campaign workspace.

## Findings

- V1 is production-proven and must remain compatible.
- Existing Product Truth validation requires exact approved evidence text; CQ2 preserves that rule.
- Existing V1 storyboard scenes are contiguous but lack shot-level cinematography, composition, keyboard, and explicit source grammar.
- Existing Android operations are safe at the adapter boundary but the old workflow schema is broad and action-specific validation is limited.
- Existing renderer is deterministic and secure, but its output profile had no explicit master/delivery encode controls.
- Existing QC is primarily technical/truth/asset QC; CQ2 adds planning-level creative signals without converting subjective taste into blockers.

## Codex implementation boundary

CQ2 contracts live in `src/adforge/creative_quality.py` and are additive. Existing V1 handlers were not replaced. Renderer output profiles now support explicit export kind, CRF, preset, audio bitrate, and faststart settings. New tests cover contracts, canonical IDs, redundancy, DSL rejection, bounded coordinate execution, QC heuristics, repair mapping, and profile-compatible real rendering.

No production deployment, database migration, external-worker V2 capture, or final V1/V2 campaign comparison was performed.
