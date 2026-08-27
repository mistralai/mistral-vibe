from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from vibe.core.config import MCPServer, MCPStdio

REDACTED = "<redacted>"
PLUGIN_PLACEHOLDER = "<plugin>"

_INPUT_VALUE = re.compile(
    r"input_value=.+?(?=(?:, input_type=)|$)", re.DOTALL | re.MULTILINE
)
_OPTION_NAME = re.compile(r"^--?[^\s=]+")
_URL = re.compile(r"[a-z][a-z0-9+.\-]*://[^\s\"'<>]+", re.IGNORECASE)


def redact_names(values: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(values))


def redact_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Publish an argument vector by name, the way ``env`` and headers are.

    An argument vector is not a position known to hold no credential:
    ``--api-key=sk-...`` and a bare token operand are both ordinary ways to
    configure a stdio server. The executable and every option name survive and
    every value is replaced, which keeps the shape a reader needs to recognize
    the server without carrying the secret that reaches it.
    """
    if not argv:
        return ()
    redacted = [argv[0]]
    for argument in argv[1:]:
        if _OPTION_NAME.match(argument) is None:
            redacted.append(REDACTED)
        elif "=" in argument:
            redacted.append(f"{argument.split('=', 1)[0]}={REDACTED}")
        else:
            redacted.append(argument)
    return tuple(redacted)


def _argv_values(argv: Sequence[str]) -> Iterator[str]:
    """The argument parts ``redact_argv`` replaces: operands and option values."""
    for argument in argv[1:]:
        if _OPTION_NAME.match(argument) is None:
            yield argument
        elif "=" in argument:
            yield argument.split("=", 1)[1]


def mcp_server_secrets(server: MCPServer) -> tuple[str, ...]:
    """Every value of one server declaration that may be a credential.

    The same positions the catalogue publishes by name: ``env`` values and
    argument values for a stdio server, header values and the query string for
    an HTTP one. Collected so text that quotes one back can have it removed.
    """
    if isinstance(server, MCPStdio):
        return (*server.env.values(), *_argv_values(server.argv()))
    query = urlsplit(server.url).query
    return (*server.http_headers().values(), *query.split("&"), query)


def redact_failure(message: str, secrets: Iterable[str] = ()) -> str:
    """Publish a failure Vibe did not author without the credentials it quotes.

    A client error is free text: an HTTP status error quotes the request URL,
    query string and all, and a spawn error can quote the argument vector. Both
    are positions redacted everywhere else on this path, so every URL is
    rewritten through `redact_url` and every known value is removed by name
    rather than trusted not to appear.
    """
    redacted = _URL.sub(lambda match: redact_url(match.group()), message)
    for secret in sorted(set(filter(None, secrets)), key=len, reverse=True):
        redacted = redacted.replace(secret, REDACTED)
    return redacted


def redact_values(values: Mapping[str, str]) -> dict[str, str]:
    return {name: REDACTED for name in sorted(values)}


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def sanitize_message(message: str, roots: Iterable[Path] = ()) -> str:
    sanitized = _INPUT_VALUE.sub(f"input_value={REDACTED}", message)
    candidates = sorted(
        (str(root) for root in roots), key=lambda value: len(value), reverse=True
    )
    for root in candidates:
        sanitized = sanitized.replace(root, PLUGIN_PLACEHOLDER)
    return sanitized
