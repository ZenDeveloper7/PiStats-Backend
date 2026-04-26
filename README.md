# PiStats Backend

PiStats Backend is a lightweight private HTTP JSON API for monitoring a Raspberry Pi and relaying Wake-on-LAN.

It is designed to pair with the PiStats Android app, but it is also usable as a standalone service by anything that can poll JSON endpoints.

## Features

- bearer-token auth
- localhost binding by default
- optional direct binding to the Pi Tailscale interface
- read-only monitoring endpoints
- protected Wake-on-LAN endpoint
- lightweight Linux collectors
- Docker-aware service status
- backup drive detection
- Wake-on-LAN relay over the Pi's LAN
- repeatable Pi install script

## API endpoints

- `GET /api/health`
- `GET /api/stats`
- `POST /api/wakeonlan/wake`

Example `GET /api/stats` response:

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
  "load_average": [0.21, 0.34, 0.40],
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

## Run locally

```bash
PISTATS_TOKEN=change-me python3 -m pi_backend.server
```

Default bind:

- `127.0.0.1:8787`

To expose it over Tailscale:

```bash
PISTATS_TOKEN=change-me \
PISTATS_BIND_MODE=tailscale \
PISTATS_WAKE_MAC=34:5a:60:f9:4b:96 \
python3 -m pi_backend.server
```

## Quick install on a Pi

From your development machine:

```bash
rsync -av ./ zen@pi:/home/zen/pistats-backend/
```

Then on the Pi:

```bash
ssh zen@pi
cd /home/zen/pistats-backend
sudo ./install-on-pi.sh
```

The installer:

- syncs files into the install directory
- creates or preserves `.env`
- generates a strong token automatically if one is not provided
- writes the `systemd` unit
- enables and starts `pistats.service`
- automatically selects a free port if the requested port is busy

For Wake-on-LAN, pass the PC MAC address and LAN broadcast address:

```bash
sudo ./install-on-pi.sh \
  --wake-mac '34:5a:60:f9:4b:96' \
  --wake-broadcast 192.168.1.255 \
  --wake-port 9
```

Show the generated token afterward:

```bash
grep '^PISTATS_TOKEN=' /home/zen/pistats-backend/.env
```

## Environment variables

- `PISTATS_TOKEN`
  - required for non-dev usage
  - generated automatically by the installer if `--token` is omitted
- `PISTATS_BIND_MODE`
  - one of: `localhost`, `tailscale`, `custom`
  - default: `localhost`
- `PISTATS_HOST`
  - used for `custom` mode, or as an override for `localhost`
- `PISTATS_TAILSCALE_IP`
  - optional explicit Tailscale IP override
- `PISTATS_PORT`
  - default: `8787`
- `PISTATS_SERVICES`
  - comma-separated Docker container names
  - default: `vaultwarden,trilium,samba,pihole`
- `PISTATS_BACKUP_LABEL`
  - optional preferred filesystem label
- `PISTATS_BACKUP_MOUNTPOINT`
  - optional expected mountpoint to check first
- `PISTATS_DEV_MODE`
  - set to `1` to disable auth for local development only
- `PISTATS_WAKE_MAC`
  - PC MAC address to wake, for example `34:5a:60:f9:4b:96`
- `PISTATS_WAKE_BROADCAST`
  - LAN broadcast address used for the magic packet
  - default: `192.168.1.255`
- `PISTATS_WAKE_PORT`
  - UDP port used for Wake-on-LAN
  - default: `9`

## Wake-on-LAN

The Wake-on-LAN endpoint sends a magic packet from the Pi to the configured LAN
broadcast address. It accepts either:

```text
Authorization: Bearer <token>
```

or:

```text
X-Wake-Token: <token>
```

Example:

```bash
curl -X POST -H "X-Wake-Token: change-me" http://127.0.0.1:8787/api/wakeonlan/wake
```

Success response:

```json
{
  "status": "sent",
  "broadcast": "192.168.1.255",
  "port": 9
}
```

## Install script

Examples:

```bash
sudo ./install-on-pi.sh
sudo ./install-on-pi.sh --port 8788
sudo ./install-on-pi.sh --bind-mode localhost
sudo ./install-on-pi.sh --wake-mac '34:5a:60:f9:4b:96' --wake-broadcast 192.168.1.255
sudo ./install-on-pi.sh --force-env
```

Port behavior:

- `--port` is the preferred starting port
- if that port is occupied, the installer walks upward until it finds a free port
- the selected port is written to `.env` and printed at the end

## Deployment docs

- [Pi Deployment Guide](docs/PI_DEPLOYMENT.md)
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
- [systemd example](pistats.service.example)
- [sample env file](.env.example)

## License

[MIT](LICENSE)
