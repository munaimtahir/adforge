# Warranty Vault — Authoritative Handoff Request

Status: **NOT READY — AUTHORITATIVE PRODUCT INPUT REQUIRED**

This request instantiates `prompts/PRODUCT_TRUTH_HANDOFF_PROMPT.md`. The supplying
app-build/release owner must inspect the current verified implementation and current
release documentation. Planned, remembered, or inferred behavior is not acceptable.

## Required return package

Place the following under `products/warranty-vault/truth/` and related asset folders:

1. `PRODUCT_TRUTH.json` conforming to `schemas/product_truth.schema.json`.
2. `PRODUCT_TRUTH.md` with CURRENT, PLANNED, DEPRECATED, and UNKNOWN distinctions.
3. `APP_CAPTURE_WORKFLOWS.md` with fictional-data-safe navigation and expected screens.
4. `CLAIM_EVIDENCE.md` mapping every approved claim to current APK/source/release evidence.
5. Current signed/release-candidate APK under `products/warranty-vault/apk/`, with:
   - source path;
   - SHA-256;
   - package ID;
   - version name and version code.
6. Exact logo, icon, fonts/colors, screenshots, and approved brand references under
   `products/warranty-vault/assets/brand/` or `assets/references/`.
7. CTA/store destination and geographic, platform, privacy, or legal limitations.
8. Explicit prohibited/unsupported claims, including those in historical material.
9. A capture-safe fictional dataset and confirmation no real private data is used.
10. Final readiness verdict: READY or NOT READY, owner, and verification timestamp.

## Evidence bar

- Every `approved_features` entry needs matching evidence whose `status` is `CURRENT`,
  whose `claim` text matches, and whose `source` identifies verifiable evidence.
- Privacy/security wording needs direct current evidence.
- If the locked brief implies unestablished behavior, mark it UNKNOWN; AdForge omits it.
- APK checksum and package/version data must match the exact supplied file.

The acceptance campaign cannot start until AdForge validates this package and the
product record changes from `UNKNOWN` to `READY`.
