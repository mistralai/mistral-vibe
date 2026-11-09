from __future__ import annotations

from http import HTTPStatus

# Redundant aliases: the helpers now live in ``_time`` (so the legacy session
# importer can read them without importing this module), but existing callers
# still import them from here.
from vibe.app_server._time import (
    iso_from_time_ms as iso_from_time_ms,
    now_ms as now_ms,
    optional_time_ms as optional_time_ms,
    time_ms as time_ms,
)

# The statuses a provider refuses a credential with. Mirrors the Harness's
# ``_REJECTION_REASONS`` so both backends classify a 403 the same way; neither
# is worth a retry or a Sentry report.
_REFUSED_CREDENTIAL_STATUSES = frozenset({
    HTTPStatus.UNAUTHORIZED,
    HTTPStatus.FORBIDDEN,
})
