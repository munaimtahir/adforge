# Implementation Quality Gates

Every phase of the build sprint has mandatory gates.

Global gate rules:
1. A phase does not pass with any mandatory gate failing.
2. On failure, diagnose → fix → rerun the failed gate and relevant regression gates.
3. Continue autonomously when gates pass.
4. Do not wait for user approval between normal phases.
5. If a step genuinely requires unavailable user input, record it in `BLOCKERS.md`, skip only that blocked item, and continue all independent work.
6. No fake/mock success may be used to satisfy an integration acceptance gate.
7. `git diff --check`, automated tests, configuration validation, and documentation update are required before each phase commit.
