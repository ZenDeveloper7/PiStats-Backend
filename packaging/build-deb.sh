#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repository_root}"

if ! command -v dpkg-buildpackage >/dev/null 2>&1; then
  echo "dpkg-buildpackage is required. On Debian/Raspberry Pi OS:" >&2
  echo "  sudo apt install build-essential debhelper devscripts" >&2
  exit 1
fi

package_version="$(dpkg-parsechangelog -S Version)"
if [[ $# -gt 0 && "$1" != "${package_version}" && "$1" != "v${package_version}" ]]; then
  echo "Requested version $1 does not match debian/changelog (${package_version})." >&2
  exit 1
fi

dpkg-buildpackage --build=binary --unsigned-source --unsigned-changes
echo "Built ../pistats-backend_${package_version}_all.deb"
