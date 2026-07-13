#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 4 ]; then
  echo "Usage: $0 <os> <arch> <version> <binary-name> [<binary-name> ...]" >&2
  exit 2
fi

os="$1"
arch="$2"
version="$3"
shift 3
release_dir="dist/release-assets"

zip_bundle() {
  local binary_base="$1"
  local bundle_dir="dist/${binary_base}-dir"
  local output_path
  output_path="$(pwd)/$release_dir/${binary_base}-${os}-${arch}-${version}.zip"

  if [ ! -d "$bundle_dir" ]; then
    echo "Missing PyInstaller bundle: $bundle_dir" >&2
    exit 1
  fi

  chmod -f +x "$bundle_dir/$binary_base" "$bundle_dir/$binary_base.exe" 2>/dev/null || true

  if [ "$os" = "windows" ]; then
    (cd "$bundle_dir" && 7z a -tzip -bd -bb0 "$output_path" .)
    return
  fi

  # The y flag stores framework symlinks as symlinks in macOS release zips.
  (cd "$bundle_dir" && zip -qry "$output_path" .)
}

rm -rf "$release_dir"
mkdir -p "$release_dir"

archive_command="zip"
if [ "$os" = "windows" ]; then
  archive_command="7z"
fi

if ! command -v "$archive_command" >/dev/null 2>&1; then
  echo "$archive_command is required to package PyInstaller artifacts on $os" >&2
  exit 1
fi

for binary_base in "$@"; do
  zip_bundle "$binary_base"
done
