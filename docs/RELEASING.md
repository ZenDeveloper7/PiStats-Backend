# Maintainer release guide

This guide describes how to publish a PiStats Backend Debian release and update
the signed APT repository.

## One-time repository setup

1. Configure GitHub Pages with **Source: GitHub Actions**.
2. Create a dedicated signing key whose private material is used only for the
   PiStats APT archive.
3. Store its ASCII-armored private key in the repository Actions secret
   `APT_GPG_PRIVATE_KEY`.
4. Publish and retain the public fingerprint independently so users can verify
   the downloaded key.

The current archive signing fingerprint is:

```text
B2E8 ED59 05E0 ECDF 7D46 7224 DCA1 E5E6 984B 664E
```

Back up the private key and revocation certificate securely. Losing the private
key prevents signing future repository metadata; losing control of it requires
an announced key rotation.

## Prepare a release

1. Choose a Debian-compatible version such as `1.1.0`.
2. Add a new top entry to `debian/changelog` with that exact version.
3. Update user-facing release notes in `docs/RELEASE_NOTES.md`.
4. Update version-specific installation examples when necessary.
5. Run the tests:

   ```bash
   python3 -m unittest discover -s tests -v
   ```

6. Build and inspect the package on Debian or Raspberry Pi OS:

   ```bash
   sudo apt install build-essential debhelper devscripts lintian
   ./packaging/build-deb.sh
   lintian --profile debian --fail-on error ../pistats-backend_*.changes
   ```

The repository also includes `packaging/Dockerfile.deb-build` for a clean
Bookworm build environment.

## Publish

Commit the release, create an annotated tag matching the changelog, and push in
that order:

```bash
git push origin main
git tag -a v1.1.0 -m "PiStats Backend 1.1.0"
git push origin v1.1.0
```

Do not move a published release tag. If users could already have downloaded a
release, fix it with a new version instead.

## Automation flow

Pushing `v*` triggers `.github/workflows/release-deb.yml`, which:

1. verifies the tag equals the version in `debian/changelog`;
2. installs the complete Debian build toolchain;
3. runs tests through `dpkg-buildpackage`;
4. runs Lintian with the Debian profile;
5. creates `SHA256SUMS` and workflow artifacts; and
6. creates the GitHub Release with the `.deb`, `.changes`, `.buildinfo`, and
   checksum files.

After a successful tagged build, `.github/workflows/publish-apt.yml`:

1. downloads `.deb` files from GitHub Releases;
2. imports `APT_GPG_PRIVATE_KEY`;
3. builds `stable` repository indexes;
4. creates signed `InRelease` and `Release.gpg` metadata; and
5. deploys the privacy page and `/apt` repository through GitHub Pages.

Manual workflow dispatch builds are useful for validation, but only a tag build
creates a GitHub Release. A manually dispatched APT workflow republishes the
existing released packages.

## Verify publication

Confirm both workflows are green and inspect the release assets. Then verify the
live repository signature:

```bash
tmpdir="$(mktemp -d)"
curl -fsSL -o "${tmpdir}/keyring.gpg" \
  https://zendeveloper7.github.io/PiStats-Backend/apt/pistats-archive-keyring.gpg
curl -fsSL -o "${tmpdir}/InRelease" \
  https://zendeveloper7.github.io/PiStats-Backend/apt/dists/stable/InRelease
gpgv --keyring "${tmpdir}/keyring.gpg" "${tmpdir}/InRelease"
```

Finally, install from the public APT repository on a clean supported Debian or
Raspberry Pi OS system and confirm the reported version:

```bash
apt-cache policy pistats-backend
dpkg-query -W pistats-backend
```

## Signing-key rotation

Key rotation is a separate release operation:

1. generate and securely back up the replacement key;
2. update `APT_GPG_PRIVATE_KEY`;
3. publish both old and new public keys during a transition when possible;
4. update the documented fingerprint and installation guide;
5. republish the repository; and
6. clearly announce the trust change in release notes.

