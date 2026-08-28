# Production Ledger Specification

Use append-only JSONL plus human-readable UI.

Each event should include where relevant:
- timestamp
- campaign_id
- stage
- task_id
- event_type
- provider/model
- prompt/reference ID
- input asset IDs
- output asset IDs
- attempt
- estimated/known credits
- status
- QC result
- failure summary
- repair action
- software/tool version

The final campaign record must identify:
- Product Truth snapshot
- APK checksum/version
- final render spec
- exact final files
- assets used in final
