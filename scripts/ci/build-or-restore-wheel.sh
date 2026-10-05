#!/usr/bin/env bash
set -euo pipefail

# CI only. Builds the mistral-vibe wheel once per unique source tree and
# leaves it in a cache directory, so a cache hit skips the expensive
# Rust-compiled PEP 517 build.
#
# Usage: build-or-restore-wheel.sh <cache-dir>
# Prints the wheel path on stdout. Nothing after this may run a plain
# `uv sync`: it is exact and prunes the pip-installed wheel from the venv.

cache_dir="${1:?usage: build-or-restore-wheel.sh <cache-dir>}"

shopt -s nullglob
existing=("$cache_dir"/mistral_vibe-*.whl)
shopt -u nullglob

if [ "${#existing[@]}" -gt 1 ]; then
  echo "Multiple cached wheels in $cache_dir; refusing to guess:" >&2
  printf '  %s\n' "${existing[@]}" >&2
  exit 1
fi

# The root project is supplied by the wheel installed below.
uv sync --no-dev --group build --no-install-project

if [ "${#existing[@]}" -eq 0 ]; then
  echo "No cached wheel; building mistral-vibe from source" >&2
  mkdir -p "$cache_dir"
  uv build --wheel --out-dir "$cache_dir"
  shopt -s nullglob
  existing=("$cache_dir"/mistral_vibe-*.whl)
  shopt -u nullglob
  if [ "${#existing[@]}" -eq 0 ]; then
    echo "No wheel produced in $cache_dir by uv build" >&2
    exit 1
  fi
fi

uv pip install --no-deps "${existing[0]}"
printf '%s\n' "${existing[0]}"
