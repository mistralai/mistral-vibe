from __future__ import annotations

import platform
from typing import TYPE_CHECKING, Any

from vibe import __version__
from vibe.core.config import VibeConfig
from vibe.core.telemetry.types import LaunchContext

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

# Injected at build time
_SENTRY_DSN = None
_SERVER_NAME = "vibe-cli"

# Benign exceptions to drop before reporting (e.g. clean Ctrl-C quit).
_FILTERED_EXCEPTIONS: tuple[type[BaseException], ...] = (KeyboardInterrupt,)

# Benign log-message prefixes to drop (e.g. asyncio GC'ing a pending task on teardown).
_FILTERED_LOG_PREFIXES: tuple[str, ...] = ("Task was destroyed but it is pending!",)


def _before_send(event: Event, hint: Hint) -> Event | None:
    exc_info = hint.get("exc_info")
    if exc_info is not None and isinstance(exc_info[1], _FILTERED_EXCEPTIONS):
        return None

    log_record = hint.get("log_record")
    if log_record is not None and log_record.getMessage().startswith(
        _FILTERED_LOG_PREFIXES
    ):
        return None

    return event


def init_sentry(
    config: VibeConfig, *, headless: bool, launch_context: LaunchContext
) -> bool:
    if not config.enable_telemetry:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration

    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        release=f"vibe@{__version__}",
        integrations=[AsyncioIntegration()],
        auto_enabling_integrations=False,
        server_name=_SERVER_NAME,  # default is socket.gethostname(). It leaks host machine's name
        include_local_variables=False,
        before_send=_before_send,
    )

    if not sentry_sdk.is_initialized():
        return False

    global_tags = {
        "headless": "true" if headless else "false",
        "os": platform.system().lower(),
        "arch": platform.machine().lower(),
    } | launch_context.sentry_tags()
    for key, value in global_tags.items():
        sentry_sdk.set_tag(key, value)
    return True


def capture_sentry_exception(
    error: BaseException,
    *,
    fatal: bool,
    tags: dict[str, str] | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    import sentry_sdk

    if not sentry_sdk.is_initialized():
        return

    with sentry_sdk.new_scope() as scope:
        scope.set_tag("fatal", "true" if fatal else "false")
        for key, value in (tags or {}).items():
            scope.set_tag(key, value)
        for key, value in (extras or {}).items():
            scope.set_extra(key, value)
        scope.capture_exception(error)
