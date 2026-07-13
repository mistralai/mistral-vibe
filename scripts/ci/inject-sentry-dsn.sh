#!/usr/bin/env bash
set -euo pipefail

target_file="${1:-vibe/core/sentry.py}"
require_sentry_dsn="${REQUIRE_SENTRY_DSN:-false}"

inject_dsn() {
  local placeholder="$1"
  local dsn="$2"
  local required="$3"

  if [ -z "${dsn}" ]; then
    if [ "${required}" = "true" ]; then
      echo "${placeholder} is required but its value is not set" >&2
      exit 1
    fi
    echo "${placeholder} value is not set; leaving it unchanged in ${target_file}"
    return 0
  fi

  if ! grep -q "^${placeholder} = None$" "${target_file}"; then
    echo "Expected ${placeholder} placeholder not found in ${target_file}" >&2
    exit 1
  fi

  local escaped_dsn
  escaped_dsn="$(printf '%s' "${dsn}" | sed 's/[&|]/\\&/g')"
  sed -i.bak "s|^${placeholder} = None$|${placeholder} = \"${escaped_dsn}\"|" "${target_file}"
  rm -f "${target_file}.bak"

  grep -q "^${placeholder} = \".*\"$" "${target_file}"
}

inject_dsn "_CLI_SENTRY_DSN" "${CLI_SENTRY_DSN:-}" "${require_sentry_dsn}"
inject_dsn "_ACP_SENTRY_DSN" "${ACP_SENTRY_DSN:-}" "${require_sentry_dsn}"
