#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <pyinstaller-bundle-dir> [<pyinstaller-bundle-dir> ...]" >&2
  exit 2
fi

for bundle_dir in "$@"; do
  internal_dir="${bundle_dir}/_internal"
  binary_name="$(basename "${bundle_dir}")"
  binary_name="${binary_name%-dir}"
  binary_path="${bundle_dir}/${binary_name}"

  find "${internal_dir}" -name '*.so*' -type f -print0 | while IFS= read -r -d '' library; do
    patchelf --clear-execstack "${library}"
  done

  if [ -f "${binary_path}" ]; then
    patchelf --clear-execstack "${binary_path}" || true
  fi
done
