"""How the app server reports a config the user can fix.

Session reads, session starts and the host's picker reads all ask
``user_config_error`` what to answer, so the same config gets the same error
whichever request loads it. Each caller raises it as its own request failure.
"""

from __future__ import annotations

from dataclasses import dataclass
import tomllib

from pydantic import JsonValue, ValidationError
from pydantic_core import ErrorDetails

from vibe.app_server.protocol import ProtocolErrorCode
from vibe.core.config._setting_keys import UnusedConfigKeysError
from vibe.core.config.layer import ConfigStorageError, LayerImplementationError
from vibe.core.config.vibe_schema import VibeConfigSchema

USER_CONFIG_ERRORS = (
    ConfigStorageError,
    LayerImplementationError,
    UnusedConfigKeysError,
    ValidationError,
)
"""The config failures ``user_config_error`` sorts into the user's or internal."""


@dataclass(frozen=True, slots=True)
class UserConfigError:
    """The protocol error for a config file the user can fix."""

    code: ProtocolErrorCode
    message: str
    data: JsonValue


def user_config_error(
    exc: ConfigStorageError
    | LayerImplementationError
    | UnusedConfigKeysError
    | ValidationError,
) -> UserConfigError | None:
    """The configuration error to answer for ``exc``, or None if it is internal.

    Not a server failure: clients show it as one.
    """
    if isinstance(exc, UnusedConfigKeysError):
        # strict_config refused settings Vibe does not use: the user's to fix.
        return _configuration_error(str(exc))
    if isinstance(exc, ValidationError):
        # A refusal from any other model is not the user's config.
        if exc.title != VibeConfigSchema.__name__:
            return None
        issues = "; ".join(_config_issue(issue) for issue in exc.errors())
        return _configuration_error(f"Cannot load the Vibe config. {issues}")
    if not _is_user_config_error(exc):
        return None
    cause = f": {exc.__cause__}" if exc.__cause__ is not None else ""
    return _configuration_error(f"Cannot load the Vibe config. {exc}{cause}")


def _configuration_error(message: str) -> UserConfigError:
    return UserConfigError(
        ProtocolErrorCode.INVALID_PARAMS, message, {"kind": "configuration"}
    )


def _config_issue(issue: ErrorDetails) -> str:
    """One refused setting, named once, without pydantic's ``Value error,``.

    Each issue reads as its setting and the validator's own words, so a client
    shows what to fix.
    """
    message = issue["msg"].removeprefix("Value error, ")
    setting = ".".join(str(part) for part in issue["loc"])
    if not setting or message.startswith(f"{setting}:"):
        return message
    return f"{setting}: {message}"


def _is_user_config_error(exc: ConfigStorageError | LayerImplementationError) -> bool:
    """Whether a layer failure is a config file the user can fix."""
    if isinstance(exc, ConfigStorageError):
        return True
    return isinstance(exc.__cause__, tomllib.TOMLDecodeError)
