# Failure and Recovery

## Retry rule
For an ordinary operation:
- Attempt 1
- Retry 1
- Retry 2

After two retries fail:
- preserve all state
- capture diagnostics
- mark affected task BLOCKED/WAITING
- report failure and attempted remedies
- request user advice only when no autonomous safe path remains

## Campaign resilience
- No campaign restart from zero for a local failure.
- Use idempotent tasks.
- Store checksums and task completion markers.
- Detect already-valid outputs after restart.
- Never overwrite a known-good asset during a retry without versioning.
