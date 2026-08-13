# Debian package and APT repository

PiStats publishes an architecture-independent Debian package for Raspberry Pi
OS and other Debian-based systems with Python 3.11 or newer.

## Package layout

```text
/usr/lib/pistats/                  backend code
/usr/bin/pistats-backend          executable
/etc/pistats/pistats.env          generated, persistent configuration
/var/lib/pistats/                 private service state
/lib/systemd/system/pistats-backend.service
```

The package creates a dedicated `pistats` system user. It does not preconfigure
Docker containers, backup devices, media paths, Wake-on-LAN hardware, or network
exposure. A unique bearer token and localhost-only binding are generated on the
first installation.

## Build locally

On Debian or Raspberry Pi OS:

```bash
sudo apt install build-essential debhelper devscripts lintian
./packaging/build-deb.sh
lintian ../pistats-backend_1.0.0_all.changes
```

Install the result:

```bash
sudo apt install ../pistats-backend_1.0.0_all.deb
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

## Release a `.deb`

1. Update the version and entry in `debian/changelog`.
2. Commit the release.
3. Tag the same version, for example `v1.0.0`.
4. Push the commit and tag.

The `Release Debian package` workflow runs the tests, builds the package, runs
Lintian, generates `SHA256SUMS`, and attaches the artifacts to the GitHub
Release. A successful tagged release workflow then triggers APT publishing.

## Publish the APT repository

Create a dedicated GPG signing key and export its private key as ASCII armor:

```bash
gpg --batch --pinentry-mode loopback --passphrase '' --quick-generate-key \
  "PiStats APT Repository <packages@example.com>" rsa4096 sign 2y
gpg --armor --export-secret-keys "PiStats APT Repository" >pistats-apt-private.asc
```

Store the complete file contents in the GitHub Actions secret
`APT_GPG_PRIVATE_KEY`. Protect that secret as a release credential; never commit
it to this repository.

In the GitHub repository settings, configure Pages to use **GitHub Actions**.
Completing the release workflow triggers `Publish APT repository`, which:

1. downloads all released `pistats-backend_*.deb` assets;
2. creates `stable` indexes for `arm64`, `armhf`, and `all`;
3. signs `Release` and `InRelease`;
4. exports the public archive key; and
5. deploys the repository under `/apt` on GitHub Pages.

For this repository, users can configure it with:

```bash
curl -fsSL https://zendeveloper7.github.io/PiStats-Backend/apt/pistats-archive-keyring.gpg \
  | sudo tee /usr/share/keyrings/pistats-archive-keyring.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/pistats-archive-keyring.gpg] https://zendeveloper7.github.io/PiStats-Backend/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/pistats.list >/dev/null

sudo apt update
sudo apt install pistats-backend
```

GitHub Pages custom workflows require the Pages artifact upload and deployment
actions used by the publishing workflow. See the
[GitHub Pages workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
