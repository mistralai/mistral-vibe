"""Redacted public view of the active model and provider.

A read-only snapshot for ``/status``: it carries no credential material and
no validation state — only what the user needs in order to see what a model
request targets.
"""

from __future__ import annotations

from vibe.app_server._model import ProtocolModel

__all__ = ["ProviderAuthView"]


class ProviderAuthView(ProtocolModel):
    model_display_name: str
    provider_name: str
    # The sanitized API base (scheme, host, optional port, path), or ``None``
    # when it is invalid or cannot be displayed safely.
    api_base: str | None = None
