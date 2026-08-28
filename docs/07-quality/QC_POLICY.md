# QC Policy — v1

v1 QC is intentionally lenient but must reject major failures.

## Mandatory blockers
- render cannot play / invalid file
- missing required scene/audio
- false or unsupported product claim
- wrong product/app
- unusable app capture
- severe AI artifact that breaks meaning
- irrelevant generated scene
- CTA absent when required
- severe audio failure
- output dimensions/duration outside accepted tolerance
- final export missing

## Advisory/repair candidates
- minor visual artifacts
- slightly weak continuity
- non-critical pacing weakness
- minor framing issue
- aesthetic imperfection

## Policy
QC thresholds are configuration, not hard-coded constants. Avoid endless regeneration. Respect generation budget.
