# Locked Decisions — AdForge v1

These decisions are authoritative unless explicitly amended and recorded in `DECISION_LEDGER.md`.

| Area | Locked decision |
|---|---|
| Product | AdForge |
| Acceptance product | Warranty Vault |
| Deployment | One dedicated Linux VM |
| Users | Single user only |
| UI | Desktop-first web app; mobile later |
| Autonomy | Fully autonomous by default |
| AI access | Subscription-first → CLI-first → browser-assisted → API-optional |
| Model routing | Claude/Codex interchangeable; route best model per task |
| Failure policy | Initial attempt + 2 retries; then pause affected stage, preserve state, report and request advice if unresolved |
| QC | Lenient v1 QC; major failures blocked, minor imperfections tolerated |
| Product truth | Mandatory and authoritative; no invented product claims |
| App ingestion | APK-first; source repository/build also supported |
| APK handling | Copy supplied APK into AdForge-owned workspace; do not mutate source project merely to ingest |
| Demo data | Fictional demo data allowed; never production/private user data |
| Emulator | One canonical Android emulator |
| Emulator fallback | Mandatory structured Capture Handoff Package |
| Ratios | 9:16, 16:9, 1:1 |
| Durations | 6, 10, 15, 20, 30 sec; 20 sec default |
| Voice | AI voice/TTS; authorized voice cloning supported |
| Text | Deterministically rendered screen text/captions/CTA |
| Music | AI-generated or properly licensed royalty-free sourcing handled by AdForge |
| Renderer | FFmpeg canonical v1; renderer adapter kept extensible |
| Video generation | Google Flow/Veo initial provider; replaceable adapter |
| Provider fallback | Generation Handoff Package; APIs optional later |
| Storage | Local persistent storage in v1 |
| Retention | Keep source/intermediate/failed assets; no pruning without user confirmation |
| Ledger | Mandatory production ledger and machine-readable asset manifest |
| Git | Source/config under Git; media normally outside Git |
| Self-improvement | May analyze/suggest; material self-code modification requires permission and documentation |
| Internet | Internet-accessible; Caddy handles reverse proxy/HTTPS externally |
| Concurrency | One active campaign at a time |
| Acceptance | Brief → autonomous pipeline → QC → finished 9:16 MP4, no external manual editing |

## Non-negotiable engineering rules

1. Product truth outranks creativity.
2. Campaign state belongs to AdForge, never to an AI session.
3. Deterministic work must be done by deterministic tools where practical.
4. External providers are adapters, not architecture.
5. Every expensive generation must be budgeted and ledgered.
6. Every campaign must be resumable after process/VM/provider interruption.
7. No silent destructive cleanup.
8. No secrets in prompts, logs, source control, manifests, or generated reports.
9. No phase in the implementation sprint passes with a failed mandatory gate.
10. Warranty Vault end-to-end acceptance is required before declaring v1 complete.
