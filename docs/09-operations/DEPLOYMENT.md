# Deployment — Caddy and systemd

AdForge runs as one unprivileged `adforge` service on a dedicated Linux VM. Uvicorn
binds only to `127.0.0.1:8080`; Caddy is the internet-facing TLS reverse proxy.

## Install

```bash
python3.12 -m venv /opt/adforge/venv
/opt/adforge/venv/bin/pip install /opt/adforge/app
/opt/adforge/venv/bin/python -m adforge.auth
sudo install -m 0644 deploy/adforge.service /etc/systemd/system/adforge.service
sudo install -m 0644 deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now adforge caddy
```

Put the generated password hash and a separately generated 32+ character secret key
in `/opt/adforge/config/adforge.env`, mode `0600`. Never put provider cookies, browser
profiles, API credentials, or raw passwords in that file's Git-tracked template.

Set `ADFORGE_DOMAIN` to the public DNS name. Caddy obtains and renews certificates;
the supplied localhost default exists only so configuration validation can run before
DNS is assigned.

## Validation

```bash
caddy validate --config deploy/Caddyfile
systemd-analyze verify deploy/adforge.service
curl --fail http://127.0.0.1:8080/login
```

The service unit uses a restrictive umask, read-only system paths, no new privileges,
and explicitly enumerated writable runtime directories. Persistent Flow profiles must
remain mode `0700` and outside Git.
