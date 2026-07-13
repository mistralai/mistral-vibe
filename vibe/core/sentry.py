from __future__ import annotations

from enum import StrEnum, auto
import platform
from typing import TYPE_CHECKING, Any, cast

from vibe import __version__
from vibe.core.config import AnyVibeConfig
from vibe.core.pii import scrub_paths
from vibe.core.telemetry.types import LaunchContext

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

# Injected at build time. Each DSN routes to its own Sentry project: the CLI/TUI
# reports to `vibe-cli`, the ACP agent to `vibe-acp`.
_CLI_SENTRY_DSN = None
_ACP_SENTRY_DSN = None


class SentryTarget(StrEnum):
    CLI = auto()
    ACP = auto()

    @property
    def dsn(self) -> str | None:
        match self:
            case SentryTarget.CLI:
                return _CLI_SENTRY_DSN
            case SentryTarget.ACP:
                return _ACP_SENTRY_DSN

    @property
    def server_name(self) -> str:
        match self:
            case SentryTarget.CLI:
                return "vibe-cli"
            case SentryTarget.ACP:
                return "vibe-acp"


# Benign exceptions to drop before reporting (e.g. clean Ctrl-C quit).
_FILTERED_EXCEPTIONS: tuple[type[BaseException], ...] = (KeyboardInterrupt,)

# Benign log-message prefixes to drop (e.g. asyncio GC'ing a pending task on teardown).
_FILTERED_LOG_PREFIXES: tuple[str, ...] = ("Task was destroyed but it is pending!",)


def _scrub_pii(event: Event) -> None:
    """Scrub personally identifiable information (PII) from the event before
    sending it to Sentry.
    """
    event_dict = cast(dict[str, Any], event)

    user = event_dict.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)

    # Breadcrumbs will contain sensitive information, e.g. tool inputs, so we drop them entirely.
    event_dict.pop("breadcrumbs", None)

    for key, value in event_dict.items():
        event_dict[key] = scrub_paths(value)


def _before_send(event: Event, hint: Hint) -> Event | None:
    exc_info = hint.get("exc_info")
    if exc_info is not None and isinstance(exc_info[1], _FILTERED_EXCEPTIONS):
        return None

    log_record = hint.get("log_record")
    if log_record is not None and log_record.getMessage().startswith(
        _FILTERED_LOG_PREFIXES
    ):
        return None

    _scrub_pii(event)
    return event


def init_sentry(
    config: AnyVibeConfig,
    *,
    headless: bool,
    launch_context: LaunchContext,
    target: SentryTarget = SentryTarget.CLI,
) -> bool:
    if not config.enable_telemetry:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration

    sentry_sdk.init(
        dsn=target.dsn,
        release=f"vibe@{__version__}",
        integrations=[AsyncioIntegration()],
        auto_enabling_integrations=False,
        server_name=target.server_name,  # default is socket.gethostname(). It leaks host machine's name
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
