"""What the Runtime decides for the MCP App of an MCP server (SEP-1865).

An app is a ``ui://`` resource a server declares in a tool's ``_meta.ui``. The
tool's visibility says whether the model, the app, or both may call it, and an
app's requests to a server the user configured run only for the fingerprint
the user consented to.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import shlex
from typing import Final, Protocol

from pydantic import JsonValue

from mistralai_vibe_local_harness.vibe._mcp_models import MCPRuntimeFailure

MODEL_AUDIENCE: Final = "model"
APP_AUDIENCE: Final = "app"
_BOTH_AUDIENCES: Final = frozenset({MODEL_AUDIENCE, APP_AUDIENCE})
MCP_APP_CONSENT_REQUIRED: Final = "mcp_app_consent_required"
MCP_APP_TOOL_DENIED: Final = "mcp_app_tool_denied"


def mcp_tool_visibility(meta: JsonValue) -> frozenset[str]:
    """Who may call a tool, from its ``_meta.ui.visibility``.

    Only a well-formed list of ``"model"`` and ``"app"`` narrows it; an absent
    or malformed one means both, as the MCP Apps specification defaults it.
    """
    ui = meta.get("ui") if isinstance(meta, dict) else None
    declared = ui.get("visibility") if isinstance(ui, dict) else None
    if not isinstance(declared, list) or not all(
        isinstance(item, str) and item in _BOTH_AUDIENCES for item in declared
    ):
        return _BOTH_AUDIENCES
    return frozenset(item for item in declared if isinstance(item, str))


class _AuthorizationIdentity(Protocol):
    @property
    def server_fingerprint(self) -> str: ...


class MCPAppServer(Protocol):
    """The parts of a resolved server config that decide what its app runs."""

    @property
    def transport(self) -> str: ...
    @property
    def url(self) -> str | None: ...
    @property
    def command(self) -> str | None: ...
    @property
    def args(self) -> tuple[str, ...]: ...
    @property
    def cwd(self) -> Path | None: ...
    @property
    def env(self) -> Mapping[str, str]: ...
    @property
    def authorization(self) -> _AuthorizationIdentity: ...
    @property
    def project_root(self) -> Path | None: ...


class MCPAppConsentServer(MCPAppServer, Protocol):
    """A server the Runtime dispatches an app's request to."""

    @property
    def name(self) -> str: ...
    @property
    def owner(self) -> str: ...


def mcp_app_fingerprint(server: MCPAppServer) -> str:
    """What decides which code a server's app runs, or where it connects.

    A stdio server's: the directory it starts in, its command (a path as the
    file it names from there; a bare program name as written, with the
    ``PATH`` its config sets, so a toolchain upgrade keeps the consent), its
    arguments, its environment variable names (not their values) and the
    project whose config declares it. Not its name, prompt or timeouts.

    An HTTP server's: its URL and its authorization fingerprint, which also
    covers its name, prompt and timeouts.
    """
    value: dict[str, object]
    if server.transport != "stdio":
        value = {
            "transport": server.transport,
            "url": server.url,
            "authorization": server.authorization.server_fingerprint,
        }
    else:
        start_dir = mcp_stdio_start_dir(server.cwd)
        value = {
            "transport": server.transport,
            "command": None
            if server.command is None
            else _resolved_program(server.command, start_dir, server.env),
            # Any argument may be a path relative to the start directory, which
            # is hashed with them.
            "args": list(server.args),
            "start_dir": str(start_dir),
            "env_names": sorted(server.env),
            "project_root": None
            if server.project_root is None
            else str(server.project_root),
        }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def mcp_stdio_start_dir(cwd: Path | None) -> Path:
    """The directory a stdio server's process starts in.

    The transport starts it in ``cwd`` (``_mcp_transport``); without one the
    process inherits the Runtime's own working directory.
    """
    if cwd is not None:
        return cwd.expanduser().resolve()
    return Path.cwd().resolve()


def _resolved_program(command: str, start_dir: Path, env: Mapping[str, str]) -> str:
    if os.sep in command or (os.altsep is not None and os.altsep in command):
        return str((start_dir / Path(command).expanduser()).resolve())
    path = env.get("PATH")
    return command if path is None else f"{command}\0PATH={path}"


def mcp_app_command(server: MCPAppServer) -> str | None:
    """A stdio server's command line as configured, for the user to see.

    Its environment is left out: values can be secrets.
    """
    if server.command is None:
        return None
    return shlex.join([server.command, *server.args])


class MCPAppConsentRequired(MCPRuntimeFailure):
    """An app's request named another fingerprint than its server's current one.

    Carries what the user is asked about: the current fingerprint, and the
    command or URL of the server the Runtime found.
    """

    def __init__(
        self, fingerprint: str, *, command: str | None = None, url: str | None = None
    ) -> None:
        self.fingerprint = fingerprint
        self.command = command
        self.url = url
        super().__init__(
            MCP_APP_CONSENT_REQUIRED,
            "The MCP server changed since the user consented to its app",
        )


class MCPAppToolDenied(MCPRuntimeFailure):
    """A ``pre_tool_call`` hook denied an app's call, as it would a model's."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(MCP_APP_TOOL_DENIED, reason or "A hook denied this tool call")


def require_mcp_app_consent(
    server: MCPAppConsentServer, expected_fingerprint: str | None
) -> None:
    """Refuse an app's request unless the user consented to ``server`` as it is.

    A config server needs ``expected_fingerprint`` to be its current one,
    whoever called; a plugin's server needs none, its install is the consent.
    """
    if server.owner == "plugin":
        return
    current = mcp_app_fingerprint(server)
    if current != expected_fingerprint:
        raise MCPAppConsentRequired(
            current, command=mcp_app_command(server), url=server.url
        )


__all__ = [
    "APP_AUDIENCE",
    "MCP_APP_CONSENT_REQUIRED",
    "MCP_APP_TOOL_DENIED",
    "MODEL_AUDIENCE",
    "MCPAppConsentRequired",
    "MCPAppConsentServer",
    "MCPAppServer",
    "MCPAppToolDenied",
    "mcp_app_command",
    "mcp_app_fingerprint",
    "mcp_stdio_start_dir",
    "mcp_tool_visibility",
    "require_mcp_app_consent",
]
