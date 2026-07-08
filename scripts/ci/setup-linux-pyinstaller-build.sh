#!/usr/bin/env bash
set -euo pipefail

# Build in manylinux for glibc compatibility, then clear executable-stack
# flags rejected by hardened Linux kernels.
python_version="${PYTHON_VERSION:-3.12}"
patchelf_version="${PATCHELF_VERSION:-0.18.0}"

uv python install "${python_version}"

arch="$(uname -m)"

# Known-good SHA256 checksums for the pinned patchelf release, keyed by "<version>-<arch>".
# Update these when bumping PATCHELF_VERSION.
declare -A patchelf_sha256=(
  ["0.18.0-x86_64"]="ce84f2447fb7a8679e58bc54a20dc2b01b37b5802e12c57eece772a6f14bf3f0"
  ["0.18.0-aarch64"]="ae13e2effe077e829be759182396b931d8f85cfb9cfe9d49385516ea367ef7b2"
)

expected_sha256="${patchelf_sha256[${patchelf_version}-${arch}]:-}"
if [[ -z "${expected_sha256}" ]]; then
  echo "No known-good SHA256 for patchelf ${patchelf_version} on ${arch}; refusing to download unverified binary." >&2
  exit 1
fi

patchelf_tarball="$(mktemp)"
trap 'rm -f "${patchelf_tarball}"' EXIT

curl -sL "https://github.com/NixOS/patchelf/releases/download/${patchelf_version}/patchelf-${patchelf_version}-${arch}.tar.gz" \
  -o "${patchelf_tarball}"

echo "${expected_sha256}  ${patchelf_tarball}" | sha256sum -c -

tar xz -C /usr/local -f "${patchelf_tarball}"

find "$(uv python dir)" -name 'libpython*.so*' -type f -exec patchelf --clear-execstack {} \;
