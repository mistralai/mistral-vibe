#!/usr/bin/env bash
set -euo pipefail

# Stage the pinned gh and glab release binaries for one build target into
# .native-build/cli-binaries/. vibe-app-server.spec collects them from there
# into _internal/vibe/bin/, where the session artifact detector looks them up
# (approach C: system PATH first, bundled binary second).
#
# Called by tools/buildkite-pipeline/scripts/vibe-acp-bundle-build.sh before
# the PyInstaller build (VIBE_BUNDLE_SET=all).
#
# Usage: download-cli-binaries.sh <os> <arch>
#   os:   linux | darwin | windows
#   arch: x86_64 | aarch64
#
# Fail-closed, like setup-linux-pyinstaller-build.sh for patchelf: an unknown
# target or a missing checksum refuses to download an unverified binary.

os="${1:?usage: download-cli-binaries.sh <os> <arch>}"
arch="${2:?usage: download-cli-binaries.sh <os> <arch>}"

gh_version="2.61.0"    # pinned — update deliberately
glab_version="1.62.0"  # pinned — update deliberately

# Anchor to the vibe/ directory (this script lives in vibe/scripts/ci/) so the
# staging dir matches SPECPATH in vibe-app-server.spec whatever the CWD is.
vibe_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
dest_dir="${vibe_root}/.native-build/cli-binaries"
mkdir -p "${dest_dir}"

# Known-good SHA256 checksums of the release archives, keyed by
# "<name>-<version>-<os>-<arch>". Cross-checked against the checksums.txt
# published with each release. A case statement (not an associative array)
# keeps this script compatible with the bash 3.2 that macOS runners provide.
checksum_for() {
  case "$1" in
    "gh-${gh_version}-linux-x86_64")   echo "e2fe1a63cef003093eb1f8e4a669e9e763bd0b747de8abb3253411b408ef6ede" ;;
    "gh-${gh_version}-linux-aarch64")  echo "1bf75ef485d8cbeca69bc18d105a52af6cd2ab2cd58708ff2d8f6b2562906d21" ;;
    "gh-${gh_version}-darwin-x86_64")  echo "794ab3aa580edf3849ff5a4f11b3e5179953958d97a6b71d7ca6fddc043c8e43" ;;
    "gh-${gh_version}-darwin-aarch64") echo "1489f762f2e0d49aa5fd8a7cf652faae5f8ac9212612b78c3180cbd88d00db25" ;;
    "gh-${gh_version}-windows-x86_64") echo "dca453982f9d826e3fdc104942f5533d334488809e54a9ae184e83cda36df4d4" ;;
    "glab-${glab_version}-linux-x86_64")   echo "2c7ad8639f121c97cecc4bd02dca731b5dede0b10a4c4810ee08b73bdd551356" ;;
    "glab-${glab_version}-linux-aarch64")  echo "490f19e65df08a41b090f6325e76abb28521c98a8339e33793eab739a05ed28a" ;;
    "glab-${glab_version}-darwin-x86_64")  echo "528a477efcb6c8984463ae63e64edfe7e6614c3a6e38ff0cc90a52ae098805e0" ;;
    "glab-${glab_version}-darwin-aarch64") echo "a419ceedcf0caf1d6768714350036de27438bc8df14779af80374307510f36c9" ;;
    "glab-${glab_version}-windows-x86_64") echo "89b1da9b8e20235dc64ac75c695da62ece5538f7cec7ac66dc246551c9dbb95b" ;;
    *)
      echo "No known-good SHA256 for $1; refusing to download an unverified binary." >&2
      return 1
      ;;
  esac
}

verify_sha256() {
  local expected="$1" file="$2"
  # macOS ships shasum instead of coreutils' sha256sum; both read the
  # "<hash>  <file>" check format.
  if command -v sha256sum >/dev/null 2>&1; then
    echo "${expected}  ${file}" | sha256sum -c -
  else
    echo "${expected}  ${file}" | shasum -a 256 -c -
  fi
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

# download_and_extract <name> <version> <url> <archive>
# Extracts the single bin/<name>[.exe] member and installs it under its
# canonical name. Archives nest the binary differently (gh tarballs and macOS
# zips use a versioned top directory, glab archives and the Windows zips put
# bin/ at the root), so the member is located with find after extraction.
download_and_extract() {
  local name="$1" version="$2" url="$3" archive="$4"
  local expected exe extract_dir binary win_archive win_extract_dir

  expected="$(checksum_for "${name}-${version}-${os}-${arch}")"

  curl -fsSL "${url}" -o "${tmp_dir}/${archive}"
  verify_sha256 "${expected}" "${tmp_dir}/${archive}"

  exe=""
  if [ "${os}" = "windows" ]; then
    exe=".exe"
  fi

  extract_dir="${tmp_dir}/extract-${name}"
  mkdir -p "${extract_dir}"
  if [ "${os}" = "windows" ]; then
    # Git Bash ships GNU tar, which cannot read the zip release assets;
    # Expand-Archive is the established Windows pattern in this repo
    # (vibe/.github/workflows/build-and-upload.yml). PowerShell only
    # resolves Windows paths, so convert the Git Bash paths with cygpath
    # first (the bundle script runs in Git Bash, where cygpath exists).
    win_archive="$(cygpath -w "${tmp_dir}/${archive}")"
    win_extract_dir="$(cygpath -w "${extract_dir}")"
    powershell -NoProfile -Command "Expand-Archive -LiteralPath '${win_archive}' -DestinationPath '${win_extract_dir}' -Force"
  else
    # GNU tar (linux) and bsdtar (darwin) both read .tar.gz and .zip.
    tar -xf "${tmp_dir}/${archive}" -C "${extract_dir}"
  fi

  binary="$(find "${extract_dir}" -type f -path "*/bin/${name}${exe}")"
  if [ -z "${binary}" ]; then
    echo "Could not find bin/${name}${exe} in ${archive}." >&2
    exit 1
  fi

  cp "${binary}" "${dest_dir}/${name}${exe}"
  chmod +x "${dest_dir}/${name}${exe}"
  echo "Staged ${dest_dir}/${name}${exe}"
}

gh_base_url="https://github.com/cli/cli/releases/download/v${gh_version}"
glab_base_url="https://gitlab.com/gitlab-org/cli/-/releases/v${glab_version}/downloads"

case "${os}-${arch}" in
  linux-x86_64)
    gh_archive="gh_${gh_version}_linux_amd64.tar.gz"
    glab_archive="glab_${glab_version}_linux_amd64.tar.gz"
    ;;
  linux-aarch64)
    gh_archive="gh_${gh_version}_linux_arm64.tar.gz"
    glab_archive="glab_${glab_version}_linux_arm64.tar.gz"
    ;;
  darwin-x86_64)
    gh_archive="gh_${gh_version}_macOS_amd64.zip"
    glab_archive="glab_${glab_version}_darwin_amd64.tar.gz"
    ;;
  darwin-aarch64)
    gh_archive="gh_${gh_version}_macOS_arm64.zip"
    glab_archive="glab_${glab_version}_darwin_arm64.tar.gz"
    ;;
  windows-x86_64)
    gh_archive="gh_${gh_version}_windows_amd64.zip"
    glab_archive="glab_${glab_version}_windows_amd64.zip"
    ;;
  *)
    echo "No CLI binaries for target ${os}-${arch}; supported: linux-x86_64, linux-aarch64, darwin-x86_64, darwin-aarch64, windows-x86_64." >&2
    exit 1
    ;;
esac

download_and_extract "gh" "${gh_version}" "${gh_base_url}/${gh_archive}" "${gh_archive}"
download_and_extract "glab" "${glab_version}" "${glab_base_url}/${glab_archive}" "${glab_archive}"

echo "CLI binaries ready in ${dest_dir}:"
ls -la "${dest_dir}"
