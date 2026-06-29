#!/usr/bin/env bash
set -euo pipefail

target_file="${1:-vibe/core/sentry.py}"
require_sentry_dsn="${REQUIRE_SENTRY_DSN:-false}"

if [ -z "${SENTRY_DSN:-}" ]; then
  if [ "${require_sentry_dsn}" = "true" ]; then
    echo "SENTRY_DSN is required but not set" >&2
    exit 1
  fi
  echo "SENTRY_DSN is not set; leaving ${target_file} unchanged"
  exit 0
fi

if ! grep -q '^_SENTRY_DSN = None$' "${target_file}"; then
  echo "Expected Sentry placeholder not found in ${target_file}" >&2
  exit 1
fi

escaped_dsn="$(printf '%s' "${SENTRY_DSN}" | sed 's/[&|]/\\&/g')"
sed -i.bak "s|^_SENTRY_DSN = None$|_SENTRY_DSN = \"${escaped_dsn}\"|" "${target_file}"
rm -f "${target_file}.bak"

grep -q '^_SENTRY_DSN = ".*"$' "${target_file}"
