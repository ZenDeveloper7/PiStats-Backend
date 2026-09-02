# OwnNode Agent brand transition

PiStats Backend is now presented as **OwnNode Agent**, the self-hosted companion
for the **OwnNode** Android app.

## No migration required

Existing installations continue to use the following compatibility identifiers:

- Debian package and systemd service: `pistats-backend`
- service account and configuration directory: `pistats`
- environment variables: `PISTATS_*`
- Python module: `pi_backend`
- API routes and `X-PiStats-*` headers
- persistent state and media bookkeeping paths

Changing these would provide no user benefit and would risk losing settings,
state, idempotency records, or seamless package upgrades.

## Repository and APT migration

The repository is now `ZenDeveloper7/OwnNode-Agent`, and its signed APT archive
is published at `https://zendeveloper7.github.io/OwnNode-Agent/apt`. GitHub
repository redirects do not cover project-site URLs, so existing installations
must update `/etc/apt/sources.list.d/pistats.list` once:

```bash
sudo sed -i 's|zendeveloper7.github.io/PiStats-Backend/apt|zendeveloper7.github.io/OwnNode-Agent/apt|' \
  /etc/apt/sources.list.d/pistats.list
sudo apt update
```

Public documentation, package descriptions, logs, and systemd descriptions use
the OwnNode Agent name. Compatibility identifiers remain documented explicitly
so administrators know that commands such as `apt install pistats-backend` are
still correct.
