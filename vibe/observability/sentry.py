from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum, auto
import errno
import platform
import re
from typing import TYPE_CHECKING, Any, cast

from vibe import __version__

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

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


# Benign exceptions to drop before reporting: clean Ctrl-C quit, and a broken
# stdout/stderr pipe (headless output consumer closed the pipe).
_FILTERED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    KeyboardInterrupt,
    BrokenPipeError,
)

# Benign exception class names to drop without importing their modules.
_FILTERED_EXCEPTION_NAMES: frozenset[str] = frozenset({
    "RealtimeTranscriptionException"
})

# Benign log-message prefixes to drop (e.g. asyncio GC'ing a pending task on teardown).
_FILTERED_LOG_PREFIXES: tuple[str, ...] = ("Task was destroyed but it is pending!",)
_PATH_RE = re.compile(
    r"(?<![\w\\/])(?:[A-Za-z]:[\\/]|~?[\\/])"
    r"(?:[^\s\"'`\\/]+(?:[ ]+[^\s\"'`\\/]+)*[\\/])+"
    r"([^\s\"'`\\/]+)"
)
_HOME_RE = re.compile(
    r"(?<![\w\\/])(?:[A-Za-z]:[\\/]|~?[\\/])(?:Users|home)[\\/]"
    r"[^\s\"'`\\/]++(?!(?:[ ]+[^\s\"'`\\/]+)*+[\\/][^\s\"'`\\/])"
)
_FILTERED = "[Filtered]"


def scrub_paths(value: Any) -> Any:
    match value:
        case str():
            return _PATH_RE.sub(rf"{_FILTERED}/\1", _HOME_RE.sub(_FILTERED, value))
        case dict():
            return {key: scrub_paths(item) for key, item in value.items()}
        case list():
            return [scrub_paths(item) for item in value]
        case tuple():
            return tuple(scrub_paths(item) for item in value)
        case _:
            return value


def _is_benign_exception(exc: BaseException) -> bool:
    if isinstance(exc, _FILTERED_EXCEPTIONS):
        return True
    if type(exc).__name__ in _FILTERED_EXCEPTION_NAMES:
        return True
    # Clean exits (sys.exit(0) / sys.exit(None)) propagating through asyncio tasks.
    if isinstance(exc, SystemExit) and exc.code in {0, None}:
        return True
    # EIO on stdio when the terminal/pty is already gone.
    if isinstance(exc, OSError) and exc.errno == errno.EIO and exc.filename is None:
        return True
    return False


def _scrub_pii(event: Event) -> None:
    event_dict = cast(dict[str, Any], event)
    user = event_dict.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)

    event_dict.pop("breadcrumbs", None)
    for key, value in event_dict.items():
        event_dict[key] = scrub_paths(value)


def _before_send(event: Event, hint: Hint) -> Event | None:
    exc_info = hint.get("exc_info")
    if exc_info is not None and _is_benign_exception(exc_info[1]):
        return None

    log_record = hint.get("log_record")
    if log_record is not None and log_record.getMessage().startswith(
        _FILTERED_LOG_PREFIXES
    ):
        return None

    _scrub_pii(event)
    return event


def init_sentry(
    *,
    enabled: bool,
    headless: bool,
    tags: Mapping[str, str],
    target: SentryTarget = SentryTarget.CLI,
) -> bool:
    if not enabled:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration

    sentry_sdk.init(
        dsn=target.dsn,
        release=f"vibe@{__version__}",
        integrations=[AsyncioIntegration()],
        auto_enabling_integrations=False,
        server_name=target.server_name,
        include_local_variables=False,
        before_send=_before_send,
    )
    if not sentry_sdk.is_initialized():
        return False

    global_tags = {
        "headless": "true" if headless else "false",
        "os": platform.system().lower(),
        "arch": platform.machine().lower(),
    } | dict(tags)
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


__all__ = ["SentryTarget", "capture_sentry_exception", "init_sentry", "scrub_paths"]
