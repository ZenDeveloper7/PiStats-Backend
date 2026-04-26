# Pi Deployment Guide

## Goal

Run the PiStats backend as a private monitoring API on your Raspberry Pi, point the Android app at it, and optionally relay Wake-on-LAN packets through the Pi.

## Assumptions

- Raspberry Pi user: `zen`
- Docker services include:
  - `vaultwarden`
  - `trilium`
  - `samba`
  - `pihole`
- You want the API kept private
- Remote access goes through Tailscale
- Your PC has Wake-on-LAN enabled if you plan to use the Wake PC button

## Copy the backend to the Pi

From your development machine:

```bash
rsync -av ./ zen@pi:/home/zen/pistats-backend/
```

## Fast install script

Once this backend repo is on the Pi, the quickest install path is:

```bash
ssh zen@pi
cd /home/zen/pistats-backend
sudo ./install-on-pi.sh --token 'replace-with-a-strong-token'
```

That script:

- syncs the backend files into the install directory
- creates or preserves `.env`
- writes the `systemd` unit
- reloads `systemd`
- enables and starts `pistats.service`

If you need a different port:

```bash
sudo ./install-on-pi.sh --port 8788 --token 'replace-with-a-strong-token'
```

If you want Wake-on-LAN configured during install:

```bash
sudo ./install-on-pi.sh \
  --token 'replace-with-a-strong-token' \
  --wake-mac '34:5a:60:f9:4b:96' \
  --wake-broadcast 192.168.1.255 \
  --wake-port 9
```

Port behavior:

- the installer treats `--port` as the preferred starting port
- if that port is already in use, it automatically walks upward to the next free port
- the chosen port is written into `/home/zen/pistats-backend/.env`
- use that same chosen port in the Android app base URL

## Configure environment

Create an env file on the Pi:

```bash
cat >/home/zen/pistats-backend/.env <<'EOF'
PISTATS_TOKEN=replace-with-a-strong-token
PISTATS_BIND_MODE=tailscale
PISTATS_PORT=8787
PISTATS_SERVICES=vaultwarden,trilium,samba,pihole
PISTATS_BACKUP_LABEL=PiBackup
PISTATS_WAKE_MAC=34:5a:60:f9:4b:96
PISTATS_WAKE_BROADCAST=192.168.1.255
PISTATS_WAKE_PORT=9
# Optional:
# PISTATS_TAILSCALE_IP=100.x.y.z
# PISTATS_BACKUP_MOUNTPOINT=/media/zen/PiBackup
EOF
```

`PISTATS_BIND_MODE=tailscale` makes the backend bind to the Pi's `tailscale0` IPv4 address so the Android app can reach it directly over Tailscale.

## Test manually on the Pi

```bash
cd /home/zen/pistats-backend
set -a
source .env
set +a
python3 -m pi_backend.server
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
sudo cp /home/zen/pistats-backend/pistats.service.example /etc/systemd/system/pistats.service
sudo systemctl daemon-reload
sudo systemctl enable --now pistats.service
```

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
  - the exact `PISTATS_TOKEN` value from the Pi

The Dashboard Wake PC button uses the configured base URL and token. The PC MAC
address stays on the Pi in `PISTATS_WAKE_MAC`; the Android app does not send or
store it.

## Security notes

- Use `PISTATS_BIND_MODE=tailscale` for Android access over Tailscale.
- Use `PISTATS_BIND_MODE=localhost` if you want the backend reachable only on the Pi itself.
- The Android app is Tailscale-only and rejects non-Tailscale base URLs.
- Do not expose this API publicly on the internet for v1.
- Keep the token strong and unique.
- Monitoring endpoints are read-only.
- `POST /api/wakeonlan/wake` is the only write-style action and is protected by
  the same token. Keep it reachable only through Tailscale or another private path.
- For extra hardening, bind to the Pi Tailscale IP and avoid public port forwarding.
