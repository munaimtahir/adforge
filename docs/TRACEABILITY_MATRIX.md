# AdForge v1 Traceability Matrix

## Implementation phases

| Requirement | Implementation | Test/evidence | Verdict |
|---|---|---|---|
| Persistent domains/database | `models.py`, `database.py`, `repository.py` | foundation CRUD/migration tests | PASS |
| Safe local storage/manifest/ledger | `storage.py`, `ledger.py`, `security.py` | traversal, isolation, schema, append/redaction tests | PASS |
| Product Truth gate/snapshot/claims | `product_truth.py` | import, evidence, immutability, approved/unknown/prohibited tests | PASS |
| State machine/retries/lease/recovery | `orchestrator.py`, `worker.py` | transition, 3-attempt, lease, idempotency, restart tests | PASS |
| Authenticated desktop UI | `web.py`, templates/static, `auth.py` | routes, PBKDF2 login, CSRF, path, lease UX tests | PASS |
| Claude/Codex routing | `providers.py` | contracts plus two real authenticated smoke calls | PASS |
| Structured creative roles | `creative.py` | schema, claim, timing, dependencies, versioning tests | PASS |
| Flow/Veo provider and handoff | `video_generation.py` | adapter/credit/retry tests; fixture handoff; live login health | PARTIAL — real generation blocked |
| APK/emulator capture and handoff | `android.py` | immutable ingest, parser, ADB safety, fixture handoff tests | PARTIAL — real device/APK blocked |
| Audio production | `audio.py` | clone authorization, WAV, timing, provenance, no-clipping tests | PASS independently |
| FFmpeg edit/render | `renderer.py` | real MP4/ffprobe, 15 profiles, text/injection/invalid tests | PASS independently |
| QC/targeted repair | `qc.py` | broken media, false claim, missing asset, advisory, budget tests | PASS independently |
| Operations/security/deployment | `operations.py`, `deploy/` | restart, backup/restore, pressure, redaction, validation | PASS |
| Warranty Vault readiness | product seed, request/report/template, web/worker guard | claim-free and blocked-start tests | PARTIAL — handoff missing |

## Definition of Done

| # | Release criterion | Evidence | Verdict |
|---:|---|---|---|
| 1 | Campaign created through desktop UI | Real campaign `f708b042-...`, HTTP 303 | PASS |
| 2 | Warranty Vault selected | Acceptance attempt database/status evidence | PASS |
| 3 | Product Truth validated/snapshotted | Truth is `UNKNOWN`; authoritative input absent | BLOCKED |
| 4 | Simple campaign brief entered | Canonical brief stored in real UI campaign | PASS |
| 5 | Strategy/script/storyboard/asset plan autonomously produced | Prevented by real Product Truth gate; fixture schemas/worker only | BLOCKED |
| 6 | AI media or real handoff proven/imported | Fixture handoff passes; no Warranty Vault generation execution | BLOCKED |
| 7 | Authentic UI capture or real handoff proven/imported | Fixture handoff passes; no Warranty Vault APK/capture | BLOCKED |
| 8 | Voice/music/SFX produced | Independent local fallback proven; not run for Warranty Vault | BLOCKED |
| 9 | Machine-readable edit plan | Typed spec proven independently; none for real campaign | BLOCKED |
| 10 | FFmpeg valid draft | Real fixture draft proven; none for real campaign | BLOCKED |
| 11 | QC records results | Real fixture QC proven; none for real campaign | BLOCKED |
| 12 | Targeted repair | Induced fixture QC repair and budget behavior proven | PASS independently |
| 13 | Final playable 9:16 MP4 | No Warranty Vault MP4 | BLOCKED |
| 14 | Ledger/manifest/truth snapshot/render spec | Contracts proven; real campaign package absent | BLOCKED |
| 15 | Controlled restart/resume | Durable fixture worker restart/resume proven | PASS independently |
| 16 | One-active-campaign enforcement | Orchestrator and web UX tests | PASS |
| 17 | No unsupported Warranty Vault claim | No ad exists; cannot validate accepted output | BLOCKED |
| 18 | No manual external editing | No real pipeline output to assess | BLOCKED |
| 19 | Automated suite/release verification | Final audit gates pass | PASS |
| 20 | Documentation matches implementation | Status/readiness/evidence/traceability updated | PASS |

Release contract result: **ADFORGE v1 — NOT READY**.
