# Product Truth Specification

Product Truth is a mandatory production gate.

## Sources, highest confidence first
1. Current verified implementation / current APK behavior
2. Current release documentation
3. Current Product Truth handoff from app-building agent
4. Current store listing
5. Approved product documentation
6. Historic campaign material

## Required fields
- product_id
- product_name
- package_id
- current_version
- description
- approved_features[]
- prohibited_claims[]
- known_limitations[]
- privacy_claims[]
- audiences[]
- brand assets
- CTA/store information
- APK/source locations
- demo workflows[]
- capture-safe demo data rules
- evidence/provenance for claims
- last_verified_at

## Hard rule
Any spoken, written, visual, or implied product claim in an advertisement must be supported by the campaign's immutable Product Truth snapshot.

## Missing truth
Unverified claims are omitted or flagged. They are never invented.
