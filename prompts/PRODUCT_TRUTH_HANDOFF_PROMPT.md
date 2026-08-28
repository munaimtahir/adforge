# Prompt — Product-Build Agent → AdForge Product Truth Handoff

You are preparing an authoritative Product Truth handoff for AdForge.

Inspect the CURRENT verified implementation and current release documentation. Do not rely on planned/backlog features as if implemented.

Produce:
1. `PRODUCT_TRUTH.json` conforming to AdForge schema.
2. `PRODUCT_TRUTH.md` readable summary.
3. `APP_CAPTURE_WORKFLOWS.md` containing safe demo workflows.
4. `CLAIM_EVIDENCE.md` mapping every approved marketing claim to implementation/release evidence.
5. APK path, version, package ID and SHA-256.
6. Brand asset paths.
7. Explicit prohibited/unsupported claims.

Rules:
- Never invent functionality.
- Distinguish CURRENT, PLANNED, DEPRECATED and UNKNOWN.
- Privacy/security claims require evidence.
- If uncertain, mark UNKNOWN and exclude from approved advertising claims.
- Finish with a Product Truth readiness verdict: READY or NOT READY.
