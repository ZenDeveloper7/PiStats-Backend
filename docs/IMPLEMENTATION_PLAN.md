# PiStats v1 Implementation Plan

## Why this backend stack

For v1, the Pi backend uses Python's standard library HTTP server instead of a heavier framework.
That keeps dependencies at zero, makes local testing immediate on Linux, and is enough for a small read-only JSON API with bearer-token auth.

## Execution order

1. Define a stable JSON contract for the Pi API.
2. Implement a localhost-bound Pi backend with read-only endpoints.
3. Test the backend locally and capture real JSON responses.
4. Build the Android client against that contract.
5. Add token auth, persisted settings, and polling.

## Backend scope

- Folder: `pi-backend/`
- Runtime: Python 3.11+ preferred, no third-party packages required
- Default bind: `127.0.0.1:8787`
- Auth: `Authorization: Bearer <token>`
- Endpoints:
  - `GET /api/health`
  - `GET /api/stats`
  - `GET /api/services`
  - `GET /api/backup-status`

## Backend implementation notes

- CPU usage: sampled from `/proc/stat`
- Memory: parsed from `/proc/meminfo`
- Disk usage: `os.statvfs("/")`
- Uptime: `/proc/uptime`
- Load average: `/proc/loadavg`
- Temperature: `/sys/class/thermal/*`
- Docker service status: `docker inspect` when Docker is available
- Backup drive state: `lsblk` + `findmnt`, with optional mountpoint/label hints from environment

## Android scope

- Single `:app` module for quick delivery
- Kotlin + Jetpack Compose
- Ktor client + Kotlinx Serialization
- DataStore for `baseUrl` and `authToken`
- Koin for DI
- Navigation with two screens:
  - Dashboard
  - Settings
- Polling every 5 seconds while dashboard is visible/configured

## Stable response contract

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
    "label": "PiBackup",
    "device": "/dev/sdb2",
    "mountpoint": null
  },
  "services": [
    { "name": "vaultwarden", "status": "up", "detail": "running" },
    { "name": "trilium", "status": "up", "detail": "running" },
    { "name": "samba", "status": "up", "detail": "running" },
    { "name": "pihole", "status": "up", "detail": "running" }
  ],
  "generated_at": "2026-04-10T12:00:00Z"
}
```

## Pi deployment outline

1. Copy `pi-backend/` to the Raspberry Pi.
2. Set `PISTATS_TOKEN`.
3. Optionally set:
   - `PISTATS_HOST`
   - `PISTATS_PORT`
   - `PISTATS_SERVICES`
   - `PISTATS_BACKUP_LABEL`
   - `PISTATS_BACKUP_MOUNTPOINT`
4. Run `python3 -m pi_backend.server`.
5. If remote access is needed, keep the API private and use Tailscale or another private path.
