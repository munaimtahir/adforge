# Deployment — Caddy and systemd

AdForge runs as one unprivileged `adforge` service on a dedicated Linux VM. Uvicorn
binds only to `127.0.0.1:8089`; Caddy is the internet-facing TLS reverse proxy.

## Install

```bash
python3.12 -m venv /opt/adforge/venv
/opt/adforge/venv/bin/pip install /opt/adforge/app
/opt/adforge/venv/bin/python -m adforge.auth
sudo install -m 0644 deploy/adforge.service /etc/systemd/system/adforge.service
sudo systemctl daemon-reload
sudo systemctl enable --now adforge
```

This VM hosts multiple applications behind one shared Caddy instance. Do not install
`deploy/Caddyfile` directly to `/etc/caddy/Caddyfile` — that file is project-managed at
`/home/munaim/srv/proxy/caddy/Caddyfile` and holds every hosted domain. Instead, add the
site block from `deploy/Caddyfile` into that file (see its own snippets: `std_headers`,
`std_log`, `std_proxy`) and apply it with
`/home/munaim/srv/proxy/caddy/sync_live_caddy.sh`, which validates, backs up, installs,
and reloads.

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
curl --fail http://127.0.0.1:8089/login
```

The service unit uses a restrictive umask, read-only system paths, no new privileges,
and explicitly enumerated writable runtime directories. Persistent Flow profiles must
remain mode `0700` and outside Git.
