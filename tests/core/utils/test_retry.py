from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http import HTTPStatus

import httpx2
import pytest

from vibe.core.utils import retry as retry_module
from vibe.core.utils.retry import (
    _is_retryable_http_error,
    _parse_retry_after,
    async_retry,
)


def _make_http_status_error(
    status_code: int, headers: dict[str, str] | None = None
) -> httpx2.HTTPStatusError:
    response = httpx2.Response(
        status_code=status_code,
        headers=headers,
        request=httpx2.Request("GET", "https://example.com"),
    )
    return httpx2.HTTPStatusError(
        message=f"Error {status_code}", request=response.request, response=response
    )


def _make_request(url: str = "https://example.com") -> httpx2.Request:
    return httpx2.Request("POST", url)


class UnrelatedStatusError(RuntimeError):
    def __init__(self, status: HTTPStatus) -> None:
        self.status = status
        super().__init__("unrelated failure")


class TestIsRetryableHttpError:
    @pytest.mark.parametrize("code", [408, 409, 425, 429, 500, 502, 503, 504, 529])
    def test_retryable_codes(self, code: int) -> None:
        assert _is_retryable_http_error(_make_http_status_error(code)) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_non_retryable_codes(self, code: int) -> None:
        assert _is_retryable_http_error(_make_http_status_error(code)) is False

    @pytest.mark.parametrize(
        "exc",
        [
            httpx2.ConnectTimeout("connect timed out", request=_make_request()),
            httpx2.ReadTimeout("read timed out", request=_make_request()),
            httpx2.WriteTimeout("write timed out", request=_make_request()),
            httpx2.PoolTimeout("pool timed out", request=_make_request()),
            httpx2.ConnectError("connection refused", request=_make_request()),
            httpx2.ReadError("read failed", request=_make_request()),
            httpx2.WriteError("write failed", request=_make_request()),
            httpx2.RemoteProtocolError("server disconnected", request=_make_request()),
        ],
    )
    def test_retryable_network_errors(self, exc: Exception) -> None:
        assert _is_retryable_http_error(exc) is True

    def test_non_retryable_request_error(self) -> None:
        assert _is_retryable_http_error(httpx2.InvalidURL("bad url")) is False

    def test_non_http_error_returns_false(self) -> None:
        assert _is_retryable_http_error(ValueError("not http")) is False

    def test_generic_exception_returns_false(self) -> None:
        assert _is_retryable_http_error(RuntimeError("boom")) is False

    def test_unrelated_status_attribute_is_not_retryable(self) -> None:
        error = UnrelatedStatusError(HTTPStatus.TOO_MANY_REQUESTS)
        assert _is_retryable_http_error(error) is False


class TestAsyncRetry:
    @pytest.mark.asyncio
    async def test_retries_network_error_then_succeeds(self) -> None:
        attempts = 0

        @async_retry(tries=3, delay_seconds=0.0, backoff_factor=1.0)
        async def call() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise httpx2.ConnectTimeout("timeout", request=_make_request())
            return "ok"

        result = await call()
        assert result == "ok"
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable(self) -> None:
        attempts = 0

        @async_retry(tries=3, delay_seconds=0.0, backoff_factor=1.0)
        async def call() -> str:
            nonlocal attempts
            attempts += 1
            raise ValueError("nope")

        with pytest.raises(ValueError):
            await call()
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_exhausts_retries(self) -> None:
        attempts = 0

        @async_retry(tries=3, delay_seconds=0.0, backoff_factor=1.0)
        async def call() -> str:
            nonlocal attempts
            attempts += 1
            raise httpx2.ReadTimeout("timeout", request=_make_request())

        with pytest.raises(httpx2.ReadTimeout):
            await call()
        assert attempts == 3


class TestParseRetryAfter:
    def test_delta_seconds(self) -> None:
        error = _make_http_status_error(429, {"Retry-After": "7"})
        assert _parse_retry_after(error) == 7.0

    def test_delta_seconds_with_surrounding_whitespace(self) -> None:
        error = _make_http_status_error(429, {"Retry-After": "  12 "})
        assert _parse_retry_after(error) == 12.0

    def test_http_date_in_the_future(self) -> None:
        future = datetime.now(UTC) + timedelta(seconds=30)
        error = _make_http_status_error(503, {"Retry-After": format_datetime(future)})
        delay = _parse_retry_after(error)
        assert delay is not None
        assert 25.0 <= delay <= 30.0

    def test_http_date_in_the_past_yields_zero(self) -> None:
        past = datetime.now(UTC) - timedelta(seconds=30)
        error = _make_http_status_error(503, {"Retry-After": format_datetime(past)})
        assert _parse_retry_after(error) == 0.0

    def test_missing_header_returns_none(self) -> None:
        assert _parse_retry_after(_make_http_status_error(429)) is None

    def test_empty_header_returns_none(self) -> None:
        assert (
            _parse_retry_after(_make_http_status_error(429, {"Retry-After": ""}))
            is None
        )

    def test_unparsable_header_returns_none(self) -> None:
        error = _make_http_status_error(429, {"Retry-After": "soon"})
        assert _parse_retry_after(error) is None

    def test_non_ascii_digit_header_returns_none(self) -> None:
        # "\xb2" (superscript two) passes str.isdigit() but float() rejects it.
        # Build via raw latin-1 header bytes, as httpx decodes them off the wire.
        response = httpx2.Response(
            status_code=429,
            headers=[(b"retry-after", b"\xb2")],
            request=httpx2.Request("GET", "https://example.com"),
        )
        error = httpx2.HTTPStatusError(
            message="Error 429", request=response.request, response=response
        )
        assert _parse_retry_after(error) is None

    def test_overflowing_http_date_returns_none(self) -> None:
        # An out-of-range year makes parsedate_to_datetime raise; must not raise.
        error = _make_http_status_error(
            429, {"Retry-After": "Mon, 01 Jan 999999999 00:00:00 GMT"}
        )
        assert _parse_retry_after(error) is None

    def test_non_http_status_error_returns_none(self) -> None:
        assert _parse_retry_after(RuntimeError("boom")) is None


class TestAdaptiveBackoff:
    @pytest.fixture
    def sleeps(self, monkeypatch: pytest.MonkeyPatch) -> list[float]:
        recorded: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            recorded.append(seconds)

        monkeypatch.setattr(retry_module.asyncio, "sleep", _fake_sleep)
        return recorded

    @pytest.mark.asyncio
    async def test_async_retry_honors_retry_after_over_exponential(
        self, sleeps: list[float]
    ) -> None:
        attempts = 0

        @async_retry(tries=3, delay_seconds=0.5, backoff_factor=2.0)
        async def call() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise _make_http_status_error(429, {"Retry-After": "5"})
            return "ok"

        assert await call() == "ok"
        assert sleeps == [5.0, 5.0]

    @pytest.mark.asyncio
    async def test_async_retry_caps_retry_after(self, sleeps: list[float]) -> None:
        @async_retry(
            tries=2, delay_seconds=0.5, backoff_factor=2.0, max_delay_seconds=10.0
        )
        async def call() -> str:
            raise _make_http_status_error(429, {"Retry-After": "3600"})

        with pytest.raises(httpx2.HTTPStatusError):
            await call()
        assert sleeps == [10.0]

    @pytest.mark.asyncio
    async def test_async_retry_falls_back_to_exponential_without_header(
        self, sleeps: list[float]
    ) -> None:
        @async_retry(tries=3, delay_seconds=0.5, backoff_factor=2.0)
        async def call() -> str:
            raise _make_http_status_error(429)

        with pytest.raises(httpx2.HTTPStatusError):
            await call()
        assert sleeps == [0.5, 1.05]
