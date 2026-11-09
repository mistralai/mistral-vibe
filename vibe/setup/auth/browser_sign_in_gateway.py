from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, auto
from typing import Literal, Protocol
from urllib.parse import SplitResult

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url_origin(parsed: SplitResult) -> tuple[str, str | None, int | None]:
    """Canonical (scheme, host, effective-port) tuple for origin comparison.

    Single source of truth for origin equality across the gateway and the
    onboarding auto-rewrite inference. Raises ``ValueError`` on a malformed
    port (mirroring ``urllib.parse.SplitResult.port``); callers that must not
    crash (e.g. UI seeding) wrap it in try/except.
    """
    scheme = parsed.scheme.lower()
    port = parsed.port
    effective_port = port if port is not None else _DEFAULT_PORTS.get(scheme)
    return scheme, parsed.hostname, effective_port


class BrowserSignInErrorCode(StrEnum):
    START_FAILED = auto()
    POLL_FAILED = auto()
    UNKNOWN_STATE = auto()
    EXCHANGE_FAILED = auto()
    MISSING_API_KEY = auto()
    MISSING_EXCHANGE_TOKEN = auto()
    EXPIRED = auto()
    DENIED = auto()
    PROVIDER_ERROR = auto()
    TIMED_OUT = auto()
    OPEN_BROWSER_FAILED = auto()


class BrowserSignInError(Exception):
    def __init__(
        self, message: str, *, code: BrowserSignInErrorCode | None = None
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class BrowserSignInProcess:
    process_id: str
    sign_in_url: str
    poll_url: str
    expires_at: datetime


@dataclass
class BrowserSignInPollResult:
    status: Literal["pending", "completed", "expired", "denied", "error"]
    exchange_token: str | None = None
    message: str | None = None


class BrowserSignInGateway(Protocol):
    async def create_process(self, code_challenge: str) -> BrowserSignInProcess: ...

    async def poll(self, poll_url: str) -> BrowserSignInPollResult: ...

    async def exchange(
        self, process_id: str, exchange_token: str, code_verifier: str
    ) -> str: ...

    async def aclose(self) -> None: ...
