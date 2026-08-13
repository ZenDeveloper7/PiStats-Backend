# Release notes

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
  modes, services, storage, and Wake-on-LAN settings are installation-specific.

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
