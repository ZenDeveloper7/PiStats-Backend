# Release notes

## v1.2.0 — 2026-08-18

This release lets authenticated clients enable or disable Wake-on-LAN without
changing the Pi-side network and hardware configuration.

### Wake-on-LAN control

- Added authenticated `GET` and `PUT /api/wakeonlan/settings` endpoints.
- Added persistent enable/disable state with atomic, power-loss-safe writes.
- Disabled wake requests now return `403` without sending a magic packet.
- Advertised client control support through the health endpoint.
- Added private state paths to both Debian and source installations while
  preserving existing configuration during upgrades.

### Verification

- Added coverage for authentication, validation, concurrent access, restart
  persistence, and unconfigured Wake-on-LAN behavior.

Release: <https://github.com/ZenDeveloper7/PiStats-Backend/releases/tag/v1.2.0>

Installation: <https://github.com/ZenDeveloper7/PiStats-Backend/blob/main/docs/INSTALLATION.md>

## v1.1.0 — 2026-08-13

This release lets each PiStats client discover Docker containers and choose
which services it wants to monitor, without requiring a predefined server-side
service list.

### Client-managed service monitoring

- Added `GET /api/services` to return Docker containers visible to the backend.
- Added the optional `services` query parameter to `GET /api/stats` for
  per-client monitoring selections.
- Kept `PISTATS_SERVICES` as a compatibility fallback for older clients.
- Replaced per-container inspection with a single `docker ps -a` query and
  normalized container states for the API.

### API and configuration hardening

- Expanded `GET /api/health` with API version and optional-feature discovery.
- Added `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` to JSON
  responses.
- Added startup validation for tokens, bind modes, ports, Wake-on-LAN settings,
  and unauthenticated development-mode exposure.
- Stopped probing or selecting arbitrary disks when backup-drive monitoring is
  not configured.
- Retained systemd hardening while allowing user-configured media, database, and
  temporary paths to remain writable.

### Documentation and tests

- Updated Android service-selection, private-network, filesystem, and privacy
  documentation.
- Added tests for service discovery, client filtering, health capabilities,
  configuration validation, disk opt-in behavior, and service-unit path access.

Release: <https://github.com/ZenDeveloper7/PiStats-Backend/releases/tag/v1.1.0>

Installation: <https://github.com/ZenDeveloper7/PiStats-Backend/blob/main/docs/INSTALLATION.md>

## v1.0.0 — 2026-08-13

The first packaged PiStats Backend release makes the service suitable for
generic installation across Raspberry Pi users rather than one predefined host.

### Installation and distribution

- Added an architecture-independent Debian package for Raspberry Pi OS and
  Debian systems with Python 3.11 or newer.
- Added a dedicated, unprivileged `pistats` system account and hardened systemd
  service.
- Added first-install generation of a unique bearer token and localhost-only
  defaults.
- Published signed `stable` APT indexes for `all`, `arm64`, and `armhf` through
  GitHub Pages.
- Added automated tag builds, tests, Lintian validation, checksums, GitHub
  Release assets, and APT deployment.
- Generalized the source installer so usernames, install paths, ports, network
  modes, storage, and Wake-on-LAN settings are installation-specific.

### Media backup API

- Added authenticated streaming uploads for Android `MediaStore` photos and
  videos.
- Added configurable MIME and size validation and path-safe, server-generated
  destinations.
- Added SQLite-backed idempotency so completed retries return `409` without
  creating duplicate files.
- Added same-filesystem temporary writes, `fsync`, and atomic finalization.
- Added root-isolated state directories, scheduled stale-upload cleanup, active
  upload protection, and configurable request-body timeouts.
- Kept partial uploads and the idempotency database outside the Samba-visible
  library.

### Generic configuration and security

- Removed predefined Docker service names, storage labels, device paths, Wake
  MAC addresses, usernames, and home-directory assumptions.
- Made Docker monitoring, media backup, drive detection, and Wake-on-LAN opt-in.
- Restricted the legacy `X-Wake-Token` header to the Wake-on-LAN endpoint;
  media uploads require bearer authentication.
- Added constant-time token comparison and preserved private-network deployment
  guidance.

### Verification

- Added configuration and media API integration tests, including authentication,
  idempotency, size validation, stale cleanup, active-upload safety, independent
  media roots, and stalled request handling.
- Verified package building and installation on Debian Bookworm.
- Verified the live APT repository signature and a clean installation of
  `pistats-backend 1.0.0` directly from the published repository.

Release: <https://github.com/ZenDeveloper7/PiStats-Backend/releases/tag/v1.0.0>

Installation: <https://github.com/ZenDeveloper7/PiStats-Backend/blob/main/docs/INSTALLATION.md>
