# Installation guide

PiStats Backend supports Raspberry Pi OS and other Debian-based systems with
Python 3.11 or newer. Installing from the PiStats APT repository is recommended:
APT verifies signed repository metadata and provides normal upgrades through
`apt upgrade`.

## Before you install

You need:

- a Raspberry Pi running a supported Debian-based OS;
- a non-root account with `sudo` access;
- network access to GitHub Pages and Debian package mirrors; and
- Tailscale on the Pi and Android device if the app will connect remotely.

The service is private by default: it binds to `127.0.0.1:8787`, generates a
unique bearer token, and enables no Docker services, media paths, backup drives,
or Wake-on-LAN hardware automatically.

## Recommended: install with APT

Install the public archive key:

```bash
curl -fsSL https://zendeveloper7.github.io/PiStats-Backend/apt/pistats-archive-keyring.gpg \
  | sudo tee /usr/share/keyrings/pistats-archive-keyring.gpg >/dev/null
```

The expected signing-key fingerprint is:

```text
B2E8 ED59 05E0 ECDF 7D46 7224 DCA1 E5E6 984B 664E
```

You can verify the downloaded key before trusting it:

```bash
gpg --show-keys --with-fingerprint \
  /usr/share/keyrings/pistats-archive-keyring.gpg
```

Add the repository and install PiStats:

```bash
echo "deb [signed-by=/usr/share/keyrings/pistats-archive-keyring.gpg] https://zendeveloper7.github.io/PiStats-Backend/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/pistats.list >/dev/null

sudo apt update
sudo apt install pistats-backend
```

The package starts `pistats-backend.service` and creates a configuration file at
`/etc/pistats/pistats.env`.

## Alternative: install a downloaded `.deb`

Download `pistats-backend_1.0.0_all.deb` and `SHA256SUMS` from the
[v1.0.0 release](https://github.com/ZenDeveloper7/PiStats-Backend/releases/tag/v1.0.0),
verify the package, and install it:

```bash
sha256sum --check --ignore-missing SHA256SUMS
sudo apt install ./pistats-backend_1.0.0_all.deb
```

This installs the same package, but future releases are not discovered
automatically unless you also configure the APT repository.

## Configure the service

Read the generated token:

```bash
sudo sed -n 's/^PISTATS_TOKEN=//p' /etc/pistats/pistats.env
```

Edit the configuration:

```bash
sudoedit /etc/pistats/pistats.env
```

For Android access over Tailscale, set:

```dotenv
PISTATS_BIND_MODE=tailscale
PISTATS_PORT=8787
```

If automatic Tailscale address discovery is unavailable, also set:

```dotenv
PISTATS_TAILSCALE_IP=100.x.y.z
```

Apply configuration changes:

```bash
sudo systemctl restart pistats-backend
sudo systemctl status pistats-backend --no-pager
```

Enter the resulting `http://100.x.y.z:8787` base URL and generated token in the
PiStats Android app. Do not expose this HTTP service directly to the public
internet; use Tailscale or another trusted private network.

## Verify the installation

Load the token without printing it and call the API locally:

```bash
PISTATS_TOKEN="$(sudo sed -n 's/^PISTATS_TOKEN=//p' /etc/pistats/pistats.env)"
curl -H "Authorization: Bearer ${PISTATS_TOKEN}" \
  http://127.0.0.1:8787/api/health
```

For a Tailscale-bound service, replace `127.0.0.1` with the Pi's Tailscale IP.
View service logs with:

```bash
sudo journalctl -u pistats-backend -n 100 --no-pager
```

## Optional Docker monitoring

Allow the dedicated service account to inspect Docker, then restart it. The
Android app will discover the containers and let each user select which ones to
monitor:

```bash
sudo usermod -aG docker pistats
sudo systemctl restart pistats-backend
```

Membership in the Docker group is effectively root-level access. Enable it only
when Docker monitoring is required.

`PISTATS_SERVICES` remains available only as a compatibility fallback for older
clients. New installations should make monitoring selections in the app.

## Optional Wake-on-LAN

Add the target computer's settings:

```dotenv
PISTATS_WAKE_MAC=00:11:22:33:44:55
PISTATS_WAKE_BROADCAST=192.168.1.255
PISTATS_WAKE_PORT=9
```

Restart the service and test it:

```bash
sudo systemctl restart pistats-backend
PISTATS_TOKEN="$(sudo sed -n 's/^PISTATS_TOKEN=//p' /etc/pistats/pistats.env)"
curl -X POST -H "X-Wake-Token: ${PISTATS_TOKEN}" \
  http://127.0.0.1:8787/api/wakeonlan/wake
```

## Optional media backup

Create a group shared by PiStats and Samba, then create the library and private
state parent on the same filesystem:

```bash
sudo groupadd --force media-backup
sudo usermod -aG media-backup pistats
sudo install -d -o root -g media-backup -m 2770 /srv/media/mobile-backups
sudo install -d -o pistats -g media-backup -m 0750 \
  /srv/media/.pistats-media-state
```

Enable the endpoint in `/etc/pistats/pistats.env`:

```dotenv
PISTATS_MEDIA_BACKUP_ROOT=/srv/media/mobile-backups
PISTATS_MEDIA_BACKUP_MAX_BYTES=1073741824
PISTATS_MEDIA_BACKUP_READ_TIMEOUT_SECONDS=300
```

The default temporary files and SQLite database are kept in a root-specific
subdirectory under `/srv/media/.pistats-media-state`. Incomplete uploads remain
outside the shared library and are removed periodically. Custom media, database,
and temporary paths may use any writable absolute location accessible to the
`pistats` service account. See the
[media backup API](MEDIA_BACKUP_API.md) for the complete protocol.

## Upgrade

APT installations upgrade normally:

```bash
sudo apt update
sudo apt upgrade
```

To upgrade only PiStats:

```bash
sudo apt install --only-upgrade pistats-backend
```

Package upgrades preserve `/etc/pistats/pistats.env`, including the token and
site-specific settings, and restart the systemd service when required.

## Remove or purge

Remove the application while retaining configuration and media/state data:

```bash
sudo apt remove pistats-backend
```

Remove the application and `/etc/pistats/pistats.env`:

```bash
sudo apt purge pistats-backend
```

PiStats deliberately retains `/var/lib/pistats` and media libraries even on
purge. Remove those only after confirming they contain nothing you need.

Remove the APT source and archive key if you no longer want updates:

```bash
sudo rm /etc/apt/sources.list.d/pistats.list
sudo rm /usr/share/keyrings/pistats-archive-keyring.gpg
sudo apt update
```

## Source installation

The source installer remains available for development and custom deployments.
Do not run it alongside the Debian package on the same port. See the
[source deployment guide](PI_DEPLOYMENT.md) for that workflow.

## Troubleshooting

- `401 unauthorized`: confirm the app token exactly matches `PISTATS_TOKEN`.
- Connection refused: check `systemctl status`, the configured bind mode, and
  the address used by the client.
- Tailscale startup failure: ensure `tailscale0` exists or configure
  `PISTATS_TAILSCALE_IP` explicitly.
- Empty service list: confirm Docker has containers and the `pistats` account is
  in the Docker group, then retry discovery in the app.
- Media endpoint returns `404`: configure `PISTATS_MEDIA_BACKUP_ROOT` and restart.
- Media startup failure: ensure the root exists, is writable by `pistats`, and
  its state/temp paths are on the same filesystem.
