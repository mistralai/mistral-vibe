"""Settings one app-server process applies to every run it serves.

A multiplexed app-server serving ``vibe run-server`` runs many sessions, each
with its own config, but the TLS trust store and the log level belong to the
process. They are set once from the server's own user config, at the first run
channel, and each run's config is held to them: another trust store fails the
run as ``config_error``, as its requests would not trust what its config says;
another log level only warns. The app-server sets up no process-wide tracer,
so tracing is not one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibe.app_server.models import ConfigIssue
from vibe.app_server.run_export import RunOutcome
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


@dataclass(frozen=True, slots=True)
class ProcessSettings:
    log_level: str | None
    enable_system_trust_store: bool

    @classmethod
    def of(cls, config: VibeConfigSchema) -> ProcessSettings:
        return cls(
            log_level=config.log_level,
            enable_system_trust_store=config.enable_system_trust_store,
        )

    def check(self, config: VibeConfigSchema) -> None:
        """Refuse a run's config the process cannot honour, and log a log
        level it will not use.

        Raises the session open error naming ``config_error``.
        """
        from vibe.app_server._agent_socket import session_open_error

        if config.enable_system_trust_store != self.enable_system_trust_store:
            raise session_open_error(
                RunOutcome.CONFIG_ERROR,
                "The run's config sets enable_system_trust_store otherwise than "
                "this app-server's own config does. The trust store applies to "
                "the whole process, so to every run: set it in the run's config "
                "as in the server's.",
            )
        if (warning := self.log_level_warning(config)) is not None:
            logger.warning(warning)

    def log_level_warning(self, config: VibeConfigSchema) -> str | None:
        if config.log_level == self.log_level:
            return None
        return (
            f"The run's config sets log_level {config.log_level!r}, but this "
            f"app-server logs at {self.log_level!r}, from its own config: the "
            "log level applies to the whole process, so to every run."
        )

    def issues(self, config: VibeConfigSchema) -> list[ConfigIssue]:
        """The run's warnings: a log level it asked for and does not get."""
        if (warning := self.log_level_warning(config)) is None:
            return []
        return [ConfigIssue(file="", message=warning)]


_pinned: ProcessSettings | None = None


async def pin_process_settings() -> ProcessSettings:
    """Set the trust store and the log level from this process's own user
    config, the first time, and return them.

    The session index is not warmed: a run channel never lists sessions.
    Channels that open together may both load the config; they pin the same.
    """
    global _pinned
    if _pinned is not None:
        return _pinned
    from vibe.core.config.default_orchestrator import build_default_orchestrator
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.observability.logging import set_config_log_level
    from vibe.utils.http import pin_ssl_context

    orchestrator = await build_default_orchestrator(
        harness_files=HarnessFilesManager(sources=("user",))
    )
    settings = ProcessSettings.of(orchestrator.config)
    pin_ssl_context(enable_system_trust_store=settings.enable_system_trust_store)
    set_config_log_level(settings.log_level)
    _pinned = settings
    return settings


__all__ = ["ProcessSettings", "pin_process_settings"]
