from __future__ import annotations

import platform
from typing import Any

from vibe import __version__
from vibe.core.config import VibeConfig
from vibe.core.telemetry.types import LaunchContext

# Injected at build time
_SENTRY_DSN = None
_SERVER_NAME = "vibe-cli"


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
        server_name=_SERVER_NAME,  # default is socket.gethostname(). It leaks host machine's name
        include_local_variables=False,
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
