# Operational Scripts

Expected scripts may include:
- environment doctor
- provider health checks
- database backup/restore
- campaign recovery
- emulator bootstrap
- ffmpeg capability check
- storage report
- handoff packager/importer

Implement during the build sprint; do not place secrets in scripts.

## Baseline commands

```bash
python3 scripts/environment_doctor.py
python3 -m pytest
ruff check .
git diff --check
```

The environment doctor emits machine-readable JSON, returns non-zero only when a
core prerequisite is missing, and reports optional integrations without exposing
credentials or account identity.
