# Pi Deployment Guide

This guide covers installation from a source checkout. Most users should use
the signed Debian repository in the [installation guide](INSTALLATION.md), which
supports normal APT upgrades and runs under a dedicated service account.

## Goal

Run the PiStats backend as a private monitoring API on your Raspberry Pi, point the Android app at it, and optionally relay Wake-on-LAN packets through the Pi.

## Assumptions

- You have a non-root Linux account on the Pi with `sudo` access.
- You want the API kept private. Tailscale is recommended for Android access,
  but localhost and custom bind addresses are also supported.
- Docker monitoring, backup-drive detection, media backup, and Wake-on-LAN are optional.

## Copy the backend to the Pi

From your development machine:

```bash
rsync -av ./ pi@raspberrypi.local:/tmp/pistats-backend/
```

Replace `pi` and `raspberrypi.local` with the target user's account and Pi
hostname. The installed application location does not depend on that username.

## Fast install script

Once this backend repo is on the Pi, the quickest install path is:

```bash
ssh pi@raspberrypi.local
cd /tmp/pistats-backend
sudo ./install-on-pi.sh --bind-mode tailscale
```

That script:

- syncs the backend files into the install directory
- installs to `/opt/pistats` by default
- runs as the non-root account that invoked `sudo` (or the `--user` value)
- creates or preserves `.env`
- generates a strong token automatically if one is not provided
- writes the `systemd` unit
- reloads `systemd`
- enables and starts `pistats.service`
- restarts the service when upgrading an existing installation

If you need a different port:

```bash
sudo ./install-on-pi.sh --port 8788
```

If you want Wake-on-LAN configured during install:

```bash
sudo ./install-on-pi.sh \
  --bind-mode tailscale \
  --wake-mac '00:11:22:33:44:55' \
  --wake-broadcast 192.168.1.255 \
  --wake-port 9
```

To enable phone media backup, pass a writable destination directory. It may be
exported separately through Samba, but that is not required by PiStats.
The installer creates a missing destination and a private state directory for
the service user:

```bash
sudo ./install-on-pi.sh \
  --bind-mode tailscale \
  --media-backup-root /srv/media/mobile-backups
```

Media backup remains disabled, and its endpoint returns `404`, when no root is
configured.

Show the generated token afterward:

```bash
sudo grep '^PISTATS_TOKEN=' /opt/pistats/.env
```

Port behavior:

- the installer treats `--port` as the preferred starting port
- if that port is already in use, it automatically walks upward to the next free port
- the chosen port is written into `/opt/pistats/.env`
- use that same chosen port in the Android app base URL

## Configure environment

Edit the installed environment file on the Pi:

```bash
sudoedit /opt/pistats/.env
```

Example configuration:

```dotenv
PISTATS_TOKEN=replace-with-a-strong-token
PISTATS_BIND_MODE=tailscale
PISTATS_PORT=8787
PISTATS_BACKUP_LABEL=MyBackupDrive
PISTATS_MEDIA_BACKUP_ROOT=/srv/media/mobile-backups
PISTATS_MEDIA_BACKUP_MAX_BYTES=1073741824
PISTATS_MEDIA_BACKUP_READ_TIMEOUT_SECONDS=300
PISTATS_WAKE_MAC=00:11:22:33:44:55
PISTATS_WAKE_BROADCAST=192.168.1.255
PISTATS_WAKE_PORT=9
# Optional:
# PISTATS_SERVICES=service-a,service-b
# PISTATS_TAILSCALE_IP=100.x.y.z
# PISTATS_BACKUP_MOUNTPOINT=/media/pi/MyBackupDrive
# PISTATS_MEDIA_BACKUP_DATABASE=/srv/media/.pistats-media-state/uploads.sqlite3
# PISTATS_MEDIA_BACKUP_TEMP_DIR=/srv/media/.pistats-media-state/tmp
# PISTATS_MEDIA_BACKUP_TEMP_MAX_AGE_SECONDS=86400
```

The media temporary directory must be outside the shared library but on the same
filesystem, which permits atomic finalization without exposing partial files.
The defaults satisfy that rule by creating a root-specific subdirectory under
`.pistats-media-state` beside the configured media root. The service user needs
write permission to both locations. Media, database, and temporary paths may be
placed under any writable absolute path; the service hardening retains access to
these installation-specific locations.

`PISTATS_BIND_MODE=tailscale` makes the backend bind to the Pi's `tailscale0` IPv4 address so the Android app can reach it directly over Tailscale.

## Test manually on the Pi

```bash
cd /opt/pistats
set -a
source .env
set +a
PISTATS_BIND_MODE=localhost python3 -m pi_backend.server
```

In another shell on the Pi:

```bash
curl -H "Authorization: Bearer $PISTATS_TOKEN" http://127.0.0.1:8787/api/health
curl -H "Authorization: Bearer $PISTATS_TOKEN" http://127.0.0.1:8787/api/stats
curl -X POST -H "X-Wake-Token: $PISTATS_TOKEN" http://127.0.0.1:8787/api/wakeonlan/wake
tailscale ip -4
```

Then from another device on your tailnet, call:

```bash
curl -H "Authorization: Bearer $PISTATS_TOKEN" http://100.x.y.z:8787/api/stats
curl -X POST -H "X-Wake-Token: $PISTATS_TOKEN" http://100.x.y.z:8787/api/wakeonlan/wake
```

## Install as a systemd service

Copy the included example service:

```bash
sudo cp /opt/pistats/pistats.service.example /etc/systemd/system/pistats.service
sudo systemctl daemon-reload
sudo systemctl enable --now pistats.service
```

Replace the `YOUR_LINUX_USER` placeholder in the example before starting it.
Normally the installer is preferable because it writes the unit automatically.

Then check:

```bash
sudo systemctl status pistats.service
journalctl -u pistats.service -n 100 --no-pager
```

## Android app configuration

In the app Settings screen, enter:

- base URL:
  - your Tailscale Pi address, for example `http://100.x.y.z:8787`
  - or a MagicDNS hostname ending in `.ts.net`
- auth token:
  - the exact `PISTATS_TOKEN` value from the Pi `.env` file

The Dashboard Wake PC button uses the configured base URL and token. The PC MAC
address stays on the Pi in `PISTATS_WAKE_MAC`; the Android app does not send or
store it.

## Security notes

- Use `PISTATS_BIND_MODE=tailscale` for Android access over Tailscale.
- Use `PISTATS_BIND_MODE=localhost` if you want the backend reachable only on the Pi itself.
- The Android app accepts HTTPS endpoints and private HTTP routes on localhost,
  LAN, `.local`, and Tailscale addresses.
- Do not expose this API publicly on the internet for v1.
- Keep the token strong and unique.
- Monitoring endpoints are read-only.
- Wake-on-LAN and media backup are the write-style endpoints. Both require the
  same token; keep them reachable only through Tailscale or another private path.
- For extra hardening, bind to the Pi Tailscale IP and avoid public port forwarding.
