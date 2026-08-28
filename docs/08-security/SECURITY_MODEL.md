# Security Model

## Principles
- Single-user does not mean unauthenticated.
- Internet exposure is expected; Caddy provides TLS/reverse proxy.
- Provider credentials/session data are secrets.
- Secrets never enter prompts, ledgers, logs, manifests, or Git.
- Persistent browser profiles use restrictive permissions.
- Commands executed by AI-controlled workers must be constrained to intended workspaces where practical.
- Uploaded APKs/assets are treated as untrusted files until validated.
- File paths must be normalized to prevent traversal.
- Generated filenames must be controlled by AdForge.
- Destructive actions require explicit internal safeguards.
- Voice cloning requires authorization/provenance.

## Self-modification
Agents may inspect and propose changes. Material changes to AdForge's production code require explicit permission before implementation.
