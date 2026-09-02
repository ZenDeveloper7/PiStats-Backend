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
- signed APT URL under `zendeveloper7.github.io/PiStats-Backend`

Changing these would provide no user benefit and would risk losing settings,
state, idempotency records, or seamless package upgrades.

## Why the repository slug remains unchanged

The repository `ZenDeveloper7/PiStats-Backend` publishes the signed APT archive
as a GitHub Pages project site. GitHub repository redirects do not cover project
site URLs. The repository therefore retains its current slug until the archive
is moved to a custom domain with a staged source-list migration for existing
installations.

Public documentation, package descriptions, logs, and systemd descriptions use
the OwnNode Agent name. Compatibility identifiers remain documented explicitly
so administrators know that commands such as `apt install pistats-backend` are
still correct.
