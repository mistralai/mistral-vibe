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

tar_gz_bundle() {
  local binary_base="$1"
  local bundle_dir="dist/${binary_base}-dir"
  local output_path
  output_path="$(pwd)/$release_dir/${binary_base}-${os}-${arch}-${version}.tar.gz"

  if [ ! -d "$bundle_dir" ]; then
    echo "Missing PyInstaller bundle: $bundle_dir" >&2
    exit 1
  fi

  chmod -f +x "$bundle_dir/$binary_base" 2>/dev/null || true

  (cd "$bundle_dir" && tar -czf "$output_path" .)
}

rm -rf "$release_dir"
mkdir -p "$release_dir"

if [ "$os" = "windows" ]; then
  if ! command -v 7z >/dev/null 2>&1; then
    echo "7z is required to package PyInstaller artifacts on $os" >&2
    exit 1
  fi
else
  if ! command -v zip >/dev/null 2>&1; then
    echo "zip is required to package PyInstaller artifacts on $os" >&2
    exit 1
  fi

  if ! command -v tar >/dev/null 2>&1; then
    echo "tar is required to package PyInstaller artifacts on $os" >&2
    exit 1
  fi
fi

for binary_base in "$@"; do
  zip_bundle "$binary_base"
  if [ "$os" != "windows" ] && [ "$binary_base" = "vibe-acp" ]; then
    tar_gz_bundle "$binary_base"
  fi
done
