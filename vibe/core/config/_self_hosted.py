from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from vibe.core.config._defaults import PUBLIC_REGIONAL_MISTRAL_API_HOSTS
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config.vibe_schema import VibeConfigSchema

_MISTRAL_DOMAIN = "mistral.ai"


def self_hosted_host(config: VibeConfigSchema) -> str | None:
    """The host of the self-hosted deployment Vibe calls Mistral through: the
    Mistral provider's ``api_base`` host when it is neither ``mistral.ai`` nor
    one of its subdomains (SaaS, regional, private cloud). ``None`` on
    Mistral's servers or without a Mistral provider.
    """
    provider = config.get_mistral_provider()
    return None if provider is None else _self_hosted_host(provider.api_base)


def _self_hosted_host(api_base: str) -> str | None:
    try:
        host = urlsplit(api_base.strip()).hostname
    except ValueError:
        return api_base
    if not host:
        return api_base
    domain = host.rstrip(".")
    if domain == _MISTRAL_DOMAIN or domain.endswith(f".{_MISTRAL_DOMAIN}"):
        return None
    return host


def experiments_allowed(config: VibeConfigSchema) -> bool:
    """Whether *config* lets Vibe ask GrowthBook: telemetry and the A/B opt-in
    are on, and the deployment is not self-hosted.
    """
    return (
        config.enable_telemetry
        and config.experiments.enable
        and self_hosted_host(config) is None
    )


def crash_reports_allowed(config: VibeConfigSchema) -> bool:
    """Whether *config* lets Vibe send crash reports to Mistral's Sentry."""
    return config.enable_telemetry and self_hosted_host(config) is None


def log_self_hosted_services(config: VibeConfigSchema) -> None:
    """Say in ``vibe.log`` that a self-hosted deployment turns off GrowthBook
    and Sentry, so a missing experiment or crash report can be explained.
    """
    # A warning so the line reaches vibe.log at the default level.
    if (host := self_hosted_host(config)) is not None:
        logger.warning(
            "Self-hosted deployment (%s): experiments and crash reports are off", host
        )


def transcription_server(api_base: str) -> str | None:
    """The realtime transcription server for a Mistral provider on
    ``api_base``: its host and port over WebSocket. ``None`` keeps the
    built-in address (see ``speech_server``).
    """
    if (server := speech_server(api_base)) is None:
        return None
    if server.startswith("https://"):
        return f"wss://{server.removeprefix('https://')}"
    return f"ws://{server.removeprefix('http://')}"


def speech_server(api_base: str) -> str | None:
    """The speech server for a Mistral provider on ``api_base``: its scheme,
    host and port, without its path. ``None`` keeps the built-in address: no
    HTTP host, or a public regional host, whose voice stays on
    ``api.mistral.ai``.
    """
    try:
        parts = urlsplit(api_base.strip())
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    if (parts.hostname or "") in PUBLIC_REGIONAL_MISTRAL_API_HOSTS:
        return None
    return f"{parts.scheme}://{parts.netloc}"
