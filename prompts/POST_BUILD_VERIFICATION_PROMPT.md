# AdForge — Independent Post-Build Verification Prompt

Act as an independent release verifier. Do not trust prior completion claims.

1. Read the complete Dev Pack and locked decisions.
2. Audit implementation against every requirement.
3. Build a traceability matrix: requirement → implementation → test/evidence → verdict.
4. Run all automated tests, lint/type checks, frontend build, schema validation and `git diff --check`.
5. Inspect state machine, retries, resume, one-active-campaign enforcement, Product Truth immutability, provider adapters, handoff protocols, APK handling, audio, renderer, QC, security, ledger and retention.
6. Verify no secrets/browser profiles/media are improperly committed.
7. Test a deterministic fixture render with FFmpeg and validate with ffprobe.
8. Test controlled restart/resume.
9. Test one targeted repair.
10. Exercise real Claude/Codex/Flow/emulator integrations when available; never substitute mocks as proof of live integration.
11. Execute the Warranty Vault acceptance campaign if authoritative Product Truth/APK/provider access are present.
12. If external requirements are missing, list exact missing evidence and keep verdict NOT READY.
13. Produce:
   - `docs/VERIFICATION_REPORT.md`
   - updated `docs/TRACEABILITY_MATRIX.md`
   - updated `docs/TEST_AND_RUNTIME_EVIDENCE.md`
   - updated `BLOCKERS.md`

Final verdict:
**ADFORGE v1 — READY** only if all mandatory acceptance criteria have real evidence.
Otherwise:
**ADFORGE v1 — NOT READY**.
