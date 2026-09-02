# Debian package and APT repository

OwnNode Agent publishes an architecture-independent Debian package for Raspberry Pi
OS and other Debian-based systems with Python 3.11 or newer.

Users should follow the [installation guide](INSTALLATION.md). This document
explains the package layout and distribution implementation; maintainers should
also read the [release guide](RELEASING.md).

## Package layout

```text
/usr/lib/pistats/                  backend code
/usr/bin/pistats-backend          executable
/etc/pistats/pistats.env          generated, persistent configuration
/var/lib/pistats/                 private service state
/var/lib/pistats/actual-cache/    optional Actual API local cache
/lib/systemd/system/pistats-backend.service
```

The package creates a dedicated `pistats` system user. It does not preconfigure
Docker containers, backup devices, media paths, Wake-on-LAN hardware, Actual
Budget credentials/accounts, or network exposure. A unique bearer token and
localhost-only binding are generated on the first installation.

## Build locally

On Debian or Raspberry Pi OS:

```bash
sudo apt install build-essential debhelper devscripts lintian
./packaging/build-deb.sh
lintian --profile debian ../pistats-backend_1.4.3_amd64.changes
```

Install the result:

```bash
sudo apt install ../pistats-backend_1.4.3_all.deb
sudoedit /etc/pistats/pistats.env
sudo systemctl restart pistats-backend
sudo systemctl status pistats-backend
```

Choose either this Debian package or the legacy `install-on-pi.sh` deployment;
do not run both services on the same port. Existing script-based installations
should be stopped and disabled before installing the package.

Read the generated token:

```bash
sudo sed -n 's/^PISTATS_TOKEN=//p' /etc/pistats/pistats.env
```

## Optional permissions

The service is deliberately unprivileged. Grant only the access a particular
installation needs.

For Docker container monitoring:

```bash
sudo usermod -aG docker pistats
sudo systemctl restart pistats-backend
```

For media backup, create a library and state directory on the same filesystem.
Use a site-specific group when Samba also needs access:

```bash
sudo groupadd --force media-backup
sudo usermod -aG media-backup pistats
sudo install -d -o root -g media-backup -m 2770 /srv/media/mobile-backups
sudo install -d -o pistats -g media-backup -m 0750 /srv/media/.pistats-media-state
```

Then add:

```dotenv
PISTATS_MEDIA_BACKUP_ROOT=/srv/media/mobile-backups
```

Actual Budget support is also optional. The package ships the bridge code but
does not download executable npm content during installation. Administrators
install the matching official API client and configure private credentials and
explicit sender-fragment/account mappings by following [Actual Budget transaction
sync](ACTUAL_BUDGET.md).

## Automated distribution

The release workflow runs tests, builds the package, runs Lintian, generates
`SHA256SUMS`, and attaches artifacts to a GitHub Release. A successful tagged
release triggers APT publishing, which:

1. downloads all released `pistats-backend_*.deb` assets;
2. creates `stable` indexes for `arm64`, `armhf`, and `all`;
3. signs `Release` and `InRelease`;
4. exports the public archive key; and
5. deploys the repository under `/apt` on GitHub Pages.

The live repository is configured with:

```bash
curl -fsSL https://zendeveloper7.github.io/OwnNode-Agent/apt/pistats-archive-keyring.gpg \
  | sudo tee /usr/share/keyrings/pistats-archive-keyring.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/pistats-archive-keyring.gpg] https://zendeveloper7.github.io/OwnNode-Agent/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/pistats.list >/dev/null

sudo apt update
sudo apt install pistats-backend
```

GitHub Pages custom workflows require the Pages artifact upload and deployment
actions used by the publishing workflow. See the
[GitHub Pages workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
