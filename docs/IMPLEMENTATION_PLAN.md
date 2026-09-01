# PiStats v1 Implementation Plan

## Why this backend stack

For v1, the Pi backend uses Python's standard library HTTP server instead of a heavier framework.
That keeps dependencies at zero, makes local testing immediate on Linux, and is enough for a small private JSON API with bearer-token auth.

## Execution order

1. Define a stable JSON contract for the Pi API.
2. Implement a localhost-bound Pi backend with read-only monitoring endpoints.
3. Test the backend locally and capture real JSON responses.
4. Build the Android client against that contract.
5. Add token auth, persisted settings, and polling.
6. Add the protected Wake-on-LAN relay once the monitoring path is stable.

## Backend scope

- Folder: this backend repository root
- Runtime: Python 3.11+ preferred, no third-party packages required
- Default bind: `127.0.0.1:8787`
- Auth: `Authorization: Bearer <token>`
- Wake auth alias: `X-Wake-Token: <token>`
- Endpoints:
  - `GET /api/health`
  - `GET /api/services`
  - `GET /api/stats`
  - `GET /api/wakeonlan/settings`
  - `PUT /api/wakeonlan/settings`
  - `POST /api/wakeonlan/wake`
  - `POST /api/media/backup/items` (optional; configured by `PISTATS_MEDIA_BACKUP_ROOT`)
  - `POST /api/transactions/sms` (installed intake; Actual import is opt-in)

## Backend implementation notes

- CPU usage: sampled from `/proc/stat`
- Memory: parsed from `/proc/meminfo`
- Disk usage: `os.statvfs("/")`
- Uptime: `/proc/uptime`
- Load average: `/proc/loadavg`
- Temperature: `/sys/class/thermal/*`
- Docker service discovery/status: one `docker ps -a` query when Docker is available
- Backup drive state: `lsblk` + `findmnt`, with optional mountpoint/label hints from environment
- Wake-on-LAN: Python UDP magic packet sent to `PISTATS_WAKE_BROADCAST:PISTATS_WAKE_PORT`
  for `PISTATS_WAKE_MAC`; its app-managed enabled state is persisted in
  `PISTATS_WAKE_STATE_FILE`; no third-party wake dependency is required
- Transaction sync: strict normalized-event validation, exact sender/account
  mapping, SQLite idempotency, serialized writes, and a subprocess bridge to
  Actual's official `@actual-app/api` Node package

## Android scope

- Single `:app` module for quick delivery
- Kotlin + Jetpack Compose
- Ktor client + Kotlinx Serialization
- DataStore for `baseUrl`, encrypted `authToken`, and selected services
- Koin for DI
- Navigation with two screens:
  - Dashboard
  - Settings
- Polling every 15 seconds while dashboard is visible/configured
- Dashboard includes a Wake PC action that posts to `/api/wakeonlan/wake`

## Stable response contract

`GET /api/health`

```json
{
  "status": "ok",
  "features": {
    "stats": true,
    "wakeonlan": false,
    "media_backup": false,
    "backup_drive": false,
    "docker_services": true,
    "service_selection": true
  }
}
```

`GET /api/stats`

```json
{
  "host": "pi",
  "uptime_seconds": 123456,
  "cpu_percent": 18.4,
  "memory": {
    "used_mb": 512,
    "total_mb": 1900
  },
  "disk": {
    "root_used_gb": 51.0,
    "root_total_gb": 917.0,
    "root_used_percent": 6.0
  },
  "temperature_c": 48.2,
  "load_average": [0.21, 0.34, 0.4],
  "backup_drive": {
    "connected": true,
    "mounted": false,
    "label": "MyBackupDrive",
    "device": "/dev/sdb2",
    "mountpoint": null
  },
  "services": [],
  "generated_at": "2026-04-10T12:00:00Z"
}
```

`POST /api/wakeonlan/wake`

```json
{
  "status": "sent",
  "broadcast": "192.168.1.255",
  "port": 9
}
```

## Pi deployment outline

1. Copy this backend repository to the Raspberry Pi.
2. Set `PISTATS_TOKEN`.
3. Optionally set:
   - `PISTATS_HOST`
   - `PISTATS_PORT`
   - `PISTATS_BACKUP_LABEL`
   - `PISTATS_BACKUP_MOUNTPOINT`
   - `PISTATS_WAKE_MAC`
   - `PISTATS_WAKE_BROADCAST`
   - `PISTATS_WAKE_PORT`
   - `PISTATS_MEDIA_BACKUP_ROOT`
   - `PISTATS_MEDIA_BACKUP_MAX_BYTES`
4. Run `python3 -m pi_backend.server`.
5. If remote access is needed, keep the API private and use Tailscale or another private path.
