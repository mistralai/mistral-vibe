"""One reading of a failed model provider call.

Provider clients report failures in their own exception types. The Mistral SDK
raises ``MistralError`` for an error response. HTTPX raises ``HTTPStatusError``
for one, and its transport errors when the connection fails. A streamed API can
also report an error as an event after its response has started, which the
adapters raise as ``ProviderStreamError``.

This module is the only place that inspects those types. The retry policy and
the failure reported to the user both read the ``ProviderFailure`` it returns,
so they cannot disagree about what happened.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from http import HTTPStatus
import json
import ssl

import httpx
from mistralai.client.errors import MistralError

from mistralai_vibe_local_harness.session_protocol import PublicRetryCategory
from mistralai_vibe_local_harness.vibe.adapters._stream_idle import (
    ProviderStreamStalled,
)


def read_provider_failure(error: BaseException) -> ProviderFailure:
    response = _error_response(error)
    return ProviderFailure(
        retryable=_is_retryable(error, response),
        stalled=isinstance(error, ProviderStreamStalled),
        category=_category(error, response),
        label=type(error).__name__ if response is None else f"HTTP {response.status}",
        message=_message(error),
        response=response,
    )


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    """What the Runtime knows about one failed provider call."""

    retryable: bool
    """Whether another attempt of the same request can succeed."""
    stalled: bool
    """Whether the response went silent after the model started answering.

    A silence can also be a long generation the provider does not stream, which
    another attempt would repeat, so the retry policy limits how often a stall
    is retried.
    """
    category: PublicRetryCategory
    label: str
    """A short name for the cause, such as ``HTTP 503`` or ``ConnectError``."""
    message: str
    """A sentence for the user that carries the provider's own explanation."""
    response: ProviderErrorResponse | None
    """The provider's error response, or ``None`` when it never answered."""


@dataclass(frozen=True, slots=True)
class ProviderErrorResponse:
    status: int
    headers: httpx.Headers


class ProviderStreamError(RuntimeError):
    """An error the provider reported inside a response stream that had started.

    ``status`` is the HTTP status that the provider's error type stands for, when
    it stands for one.
    """

    def __init__(self, message: str, status: int | None) -> None:
        super().__init__(message)
        self.status = status


class IncompleteProviderStream(RuntimeError):
    """A response stream that ended before the provider marked it finished."""


async def until_connection_lost[T](
    source: AsyncIterator[T], *, complete: Callable[[], bool]
) -> AsyncGenerator[T]:
    """Yield the items of ``source``; once ``complete()`` holds, a lost connection ends it.

    A provider sends the finish reason before the stream's end marker and any
    trailing usage. A connection lost in between leaves a complete answer, and
    retrying it would pay for the whole generation again.
    """
    iterator = aiter(source)
    while True:
        try:
            item = await anext(iterator)
        except StopAsyncIteration:
            return
        except (httpx.TransportError, ssl.SSLError):
            if not complete():
                raise
            return
        yield item


# 520 is the status an edge proxy answers with when the origin returns
# something it cannot relay; the next attempt usually succeeds.
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 529})
_RETRYABLE_TRANSPORT_ERRORS: tuple[type[httpx.TransportError], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


def _error_response(error: BaseException) -> ProviderErrorResponse | None:
    if isinstance(error, MistralError):
        return ProviderErrorResponse(status=error.status_code, headers=error.headers)
    if isinstance(error, httpx.HTTPStatusError):
        return ProviderErrorResponse(
            status=error.response.status_code, headers=error.response.headers
        )
    if isinstance(error, ProviderStreamError) and error.status is not None:
        return ProviderErrorResponse(status=error.status, headers=httpx.Headers())
    return None


def _is_retryable(error: BaseException, response: ProviderErrorResponse | None) -> bool:
    if response is not None:
        return response.status in _RETRYABLE_STATUSES
    if isinstance(error, IncompleteProviderStream):
        return True
    # A rejected certificate is the one deterministic TLS fault: every attempt
    # fails the same way. During the handshake the transport wraps it in a
    # ConnectError; while the body streams it arrives bare.
    if _rejected_certificate(error):
        return False
    # A TLS fault while the response body streams arrives as a bare
    # ``ssl.SSLError``, outside the HTTPX transport errors.
    if isinstance(error, ssl.SSLError):
        return True
    return isinstance(error, _RETRYABLE_TRANSPORT_ERRORS)


def _rejected_certificate(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _category(
    error: BaseException, response: ProviderErrorResponse | None
) -> PublicRetryCategory:
    if response is not None:
        return _status_category(response.status)
    if isinstance(error, httpx.TimeoutException):
        return "timed_out"
    if isinstance(error, _RETRYABLE_TRANSPORT_ERRORS + (ssl.SSLError,)):
        return "connection"
    return "unknown"


def _status_category(status: int) -> PublicRetryCategory:
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return "rate_limited"
    if status == HTTPStatus.REQUEST_TIMEOUT:
        return "timed_out"
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return "server_error"
    return "unknown"


def _message(error: BaseException) -> str:
    """The failure sentence, carrying the provider's own explanation.

    A status error renders as the status line and the URL, which says the
    request failed but not why. The response body usually says why, and that is
    the part a user can act on.
    """
    rendered = str(error) or _unexplained_message(error)
    explanation = _provider_explanation(error)
    if explanation is None or explanation in rendered:
        return rendered
    return f"{rendered}: {explanation}"


def _unexplained_message(error: BaseException) -> str:
    """A sentence for an exception that renders as an empty string.

    HTTPX raises its transport timeouts without a message, which would otherwise
    reach the user as a blank error.
    """
    name = type(error).__name__
    if isinstance(error, httpx.ReadTimeout):
        return f"Timed out waiting for the model to send its response ({name})."
    if isinstance(error, httpx.ConnectTimeout):
        return f"Timed out connecting to the model provider ({name})."
    if isinstance(error, httpx.TimeoutException):
        return f"The model request timed out ({name})."
    return f"The model request failed ({name})."


def _provider_explanation(error: BaseException) -> str | None:
    """The message a provider put in an error response body.

    The OpenAI-compatible ``error.message`` first, then its common variants.
    """
    body = _response_body(error)
    if body is None:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    nested = payload.get("error")
    for candidate in (
        nested.get("message") if isinstance(nested, dict) else None,
        nested if isinstance(nested, str) else None,
        payload.get("message"),
        payload.get("detail"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _response_body(error: BaseException) -> str | None:
    if isinstance(error, MistralError):
        return error.body
    if not isinstance(error, httpx.HTTPStatusError):
        return None
    try:
        return error.response.text
    except httpx.ResponseNotRead:
        # Reading here would block on a connection the failure already abandoned.
        return None


__all__ = [
    "IncompleteProviderStream",
    "ProviderErrorResponse",
    "ProviderFailure",
    "ProviderStreamError",
    "read_provider_failure",
    "until_connection_lost",
]
