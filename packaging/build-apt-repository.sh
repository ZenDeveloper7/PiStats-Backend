#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 DEB_DIRECTORY OUTPUT_DIRECTORY SUITE GPG_KEY_ID" >&2
}

if [[ $# -ne 4 ]]; then
  usage
  exit 1
fi

deb_directory="$(realpath "$1")"
output_directory="$(realpath -m "$2")"
suite="$3"
gpg_key_id="$4"

for command_name in apt-ftparchive dpkg-deb gpg gzip; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "${command_name} is required to publish the APT repository." >&2
    exit 1
  fi
done

if ! [[ "${suite}" =~ ^[a-z0-9][a-z0-9._-]*$ ]]; then
  echo "Suite contains unsupported characters: ${suite}" >&2
  exit 1
fi

shopt -s nullglob
packages=("${deb_directory}"/*.deb)
if [[ ${#packages[@]} -eq 0 ]]; then
  echo "No .deb files found in ${deb_directory}." >&2
  exit 1
fi

pool="${output_directory}/pool/main/p/pistats-backend"
distribution="${output_directory}/dists/${suite}"
mkdir -p "${pool}" "${distribution}"

for package in "${packages[@]}"; do
  if [[ "$(dpkg-deb --field "${package}" Package)" != "pistats-backend" ]]; then
    echo "Ignoring package with an unexpected name: ${package}" >&2
    continue
  fi
  cp -f "${package}" "${pool}/"
done

temporary_directory="$(mktemp -d)"
trap 'rm -rf -- "${temporary_directory}"' EXIT

cd "${output_directory}"
apt-ftparchive packages pool >"${temporary_directory}/Packages"
gzip -9c "${temporary_directory}/Packages" >"${temporary_directory}/Packages.gz"

for architecture in all arm64 armhf; do
  binary_directory="${distribution}/main/binary-${architecture}"
  mkdir -p "${binary_directory}"
  cp "${temporary_directory}/Packages" "${binary_directory}/Packages"
  cp "${temporary_directory}/Packages.gz" "${binary_directory}/Packages.gz"
done

release_config="${temporary_directory}/apt-release.conf"
cat >"${release_config}" <<EOF
APT::FTPArchive::Release {
  Origin "OwnNode";
  Label "OwnNode";
  Suite "${suite}";
  Codename "${suite}";
  Architectures "all arm64 armhf";
  Components "main";
  Description "OwnNode Agent packages";
};
EOF

apt-ftparchive -c "${release_config}" release "dists/${suite}" \
  >"${distribution}/Release"
gpg --batch --yes --local-user "${gpg_key_id}" --clearsign \
  --output "${distribution}/InRelease" "${distribution}/Release"
gpg --batch --yes --local-user "${gpg_key_id}" --armor --detach-sign \
  --output "${distribution}/Release.gpg" "${distribution}/Release"
gpg --batch --yes --local-user "${gpg_key_id}" \
  --export >"${output_directory}/pistats-archive-keyring.gpg"

echo "Published signed ${suite} repository at ${output_directory}"
