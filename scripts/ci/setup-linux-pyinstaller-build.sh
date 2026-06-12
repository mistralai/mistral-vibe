#!/usr/bin/env bash
set -euo pipefail

# Build in manylinux for glibc compatibility, then clear executable-stack
# flags rejected by hardened Linux kernels.
python_version="${PYTHON_VERSION:-3.12}"
patchelf_version="${PATCHELF_VERSION:-0.18.0}"

uv python install "${python_version}"

curl -sL "https://github.com/NixOS/patchelf/releases/download/${patchelf_version}/patchelf-${patchelf_version}-$(uname -m).tar.gz" \
  | tar xz -C /usr/local

find "$(uv python dir)" -name 'libpython*.so*' -type f -exec patchelf --clear-execstack {} \;
