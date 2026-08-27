from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http import HTTPStatus

import httpx
import pytest

from vibe.core.utils import retry as retry_module
from vibe.core.utils.retry import (
    RetryCategory,
    RetryReason,
    StreamHTTPError,
    _is_retryable_http_error,
    _next_delay,
    _parse_retry_after,
    async_generator_retry,
    async_retry,
)


def _make_http_status_error(
    status_code: int, headers: dict[str, str] | None = None
) -> httpx.HTTPStatusError:
    response = httpx.Response(
        status_code=status_code,
        headers=headers,
        request=httpx.Request("GET", "https://example.com"),
    )
    return httpx.HTTPStatusError(
        message=f"Error {status_code}", request=response.request, response=response
    )


def _make_request(url: str = "https://example.com") -> httpx.Request:
    return httpx.Request("POST", url)


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
            httpx.ConnectTimeout("connect timed out", request=_make_request()),
            httpx.ReadTimeout("read timed out", request=_make_request()),
            httpx.WriteTimeout("write timed out", request=_make_request()),
            httpx.PoolTimeout("pool timed out", request=_make_request()),
            httpx.ConnectError("connection refused", request=_make_request()),
            httpx.ReadError("read failed", request=_make_request()),
            httpx.WriteError("write failed", request=_make_request()),
            httpx.RemoteProtocolError("server disconnected", request=_make_request()),
        ],
    )
    def test_retryable_network_errors(self, exc: Exception) -> None:
        assert _is_retryable_http_error(exc) is True

    def test_non_retryable_request_error(self) -> None:
        assert _is_retryable_http_error(httpx.InvalidURL("bad url")) is False

    def test_non_http_error_returns_false(self) -> None:
        assert _is_retryable_http_error(ValueError("not http")) is False

    def test_generic_exception_returns_false(self) -> None:
        assert _is_retryable_http_error(RuntimeError("boom")) is False

    @pytest.mark.parametrize(
        "status", [HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.INTERNAL_SERVER_ERROR]
    )
    def test_retryable_stream_error_status(self, status: HTTPStatus) -> None:
        assert (
            _is_retryable_http_error(StreamHTTPError("stream failed", status)) is True
        )

    def test_non_retryable_stream_error_status(self) -> None:
        error = StreamHTTPError("stream failed", HTTPStatus.UNAUTHORIZED)
        assert _is_retryable_http_error(error) is False

    def test_unrelated_status_attribute_is_not_retryable(self) -> None:
        error = UnrelatedStatusError(HTTPStatus.TOO_MANY_REQUESTS)
        assert _is_retryable_http_error(error) is False


class TestRetryReason:
    @pytest.mark.parametrize(
        ("code", "category"),
        [
            (429, RetryCategory.RATE_LIMITED),
            (408, RetryCategory.TIMED_OUT),
            (500, RetryCategory.SERVER_ERROR),
            (502, RetryCategory.SERVER_ERROR),
            (503, RetryCategory.SERVER_ERROR),
            (504, RetryCategory.SERVER_ERROR),
            (529, RetryCategory.SERVER_ERROR),
            (409, RetryCategory.UNKNOWN),
            (425, RetryCategory.UNKNOWN),
        ],
    )
    def test_classifies_http_status(self, code: int, category: RetryCategory) -> None:
        assert RetryReason.from_http_status(code) == RetryReason(
            category, f"HTTP {code}"
        )

    def test_classifies_an_http_status_error_by_its_response(self) -> None:
        assert RetryReason.from_error(_make_http_status_error(429)) == RetryReason(
            RetryCategory.RATE_LIMITED, "HTTP 429"
        )

    def test_classifies_a_stream_error_by_its_status(self) -> None:
        error = StreamHTTPError("stream failed", HTTPStatus.TOO_MANY_REQUESTS)
        assert RetryReason.from_error(error) == RetryReason(
            RetryCategory.RATE_LIMITED, "HTTP 429"
        )

    def test_does_not_classify_an_unrelated_status_attribute(self) -> None:
        error = UnrelatedStatusError(HTTPStatus.TOO_MANY_REQUESTS)
        assert RetryReason.from_error(error) == RetryReason(
            RetryCategory.UNKNOWN, "UnrelatedStatusError"
        )

    @pytest.mark.parametrize(
        ("error", "category"),
        [
            (
                httpx.ConnectTimeout("connect timed out", request=_make_request()),
                RetryCategory.TIMED_OUT,
            ),
            (
                httpx.ReadTimeout("read timed out", request=_make_request()),
                RetryCategory.TIMED_OUT,
            ),
            (
                httpx.PoolTimeout("pool timed out", request=_make_request()),
                RetryCategory.TIMED_OUT,
            ),
            (
                httpx.ConnectError("connection refused", request=_make_request()),
                RetryCategory.CONNECTION,
            ),
            (
                httpx.ReadError("read failed", request=_make_request()),
                RetryCategory.CONNECTION,
            ),
            (
                httpx.WriteError("write failed", request=_make_request()),
                RetryCategory.CONNECTION,
            ),
            (
                httpx.RemoteProtocolError("disconnected", request=_make_request()),
                RetryCategory.CONNECTION,
            ),
            (RuntimeError("boom"), RetryCategory.UNKNOWN),
        ],
    )
    def test_classifies_transport_errors(
        self, error: Exception, category: RetryCategory
    ) -> None:
        reason = RetryReason.from_error(error)

        assert reason.category is category
        assert reason.detail == type(error).__name__


class TestAsyncRetry:
    @pytest.mark.asyncio
    async def test_retries_network_error_then_succeeds(self) -> None:
        attempts = 0

        @async_retry(tries=3, delay_seconds=0.0, backoff_factor=1.0)
        async def call() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise httpx.ConnectTimeout("timeout", request=_make_request())
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
            raise httpx.ReadTimeout("timeout", request=_make_request())

        with pytest.raises(httpx.ReadTimeout):
            await call()
        assert attempts == 3


class TestAsyncGeneratorRetry:
    @pytest.mark.asyncio
    async def test_retries_before_first_yield(self) -> None:
        attempts = 0

        @async_generator_retry(tries=3, delay_seconds=0.0, backoff_factor=1.0)
        async def gen() -> AsyncGenerator[int]:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise httpx.ConnectError("connect failed", request=_make_request())
            yield 1
            yield 2

        items = [item async for item in gen()]
        assert items == [1, 2]
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_after_first_yield(self) -> None:
        attempts = 0

        @async_generator_retry(tries=3, delay_seconds=0.0, backoff_factor=1.0)
        async def gen() -> AsyncGenerator[int]:
            nonlocal attempts
            attempts += 1
            yield 1
            raise httpx.ReadError("midstream", request=_make_request())

        items: list[int] = []
        with pytest.raises(httpx.ReadError):
            async for item in gen():
                items.append(item)

        assert items == [1]
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable_before_yield(self) -> None:
        attempts = 0

        @async_generator_retry(tries=3, delay_seconds=0.0, backoff_factor=1.0)
        async def gen() -> AsyncGenerator[int]:
            nonlocal attempts
            attempts += 1
            raise ValueError("nope")
            yield 0  # pragma: no cover

        with pytest.raises(ValueError):
            async for _ in gen():
                pass
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_closes_active_generator_when_consumer_stops(self) -> None:
        closed = False

        @async_generator_retry()
        async def gen() -> AsyncGenerator[int]:
            nonlocal closed
            try:
                yield 1
                yield 2
            finally:
                closed = True

        stream = gen()
        assert await anext(stream) == 1
        await stream.aclose()

        assert closed is True


class TestBudgetBoundedRetry:
    @pytest.fixture
    def fake_clock(self, monkeypatch: pytest.MonkeyPatch) -> list[float]:
        now = [0.0]
        monkeypatch.setattr(retry_module.time, "monotonic", lambda: now[0])

        async def _sleep(seconds: float) -> None:
            now[0] += seconds

        monkeypatch.setattr(retry_module.asyncio, "sleep", _sleep)
        return now

    def test_requires_a_bound(self) -> None:
        with pytest.raises(ValueError):
            async_retry(tries=None, max_elapsed_time=None)

    def test_next_delay_saturates_at_max_past_cap(self) -> None:
        error = httpx.ConnectError("down", request=_make_request())
        # attempt well past the point where 0.5 * 2**attempt exceeds 60s: the
        # delay must be exactly the cap, not an undershot of it.
        assert _next_delay(error, 1000, 0.5, 2.0, 60.0) == 60.0

    @pytest.mark.asyncio
    async def test_zero_budget_re_raises_after_first_attempt(
        self, fake_clock: list[float]
    ) -> None:
        attempts = 0

        @async_retry(tries=None, max_elapsed_time=0.0, delay_seconds=1.0)
        async def call() -> str:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("down", request=_make_request())

        with pytest.raises(httpx.ConnectError):
            await call()
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_budget_governs_over_count(self, fake_clock: list[float]) -> None:
        attempts = 0

        @async_retry(
            tries=None, max_elapsed_time=5.0, delay_seconds=0.5, backoff_factor=1.0
        )
        async def call() -> str:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("down", request=_make_request())

        with pytest.raises(httpx.ConnectError):
            await call()
        # tries=None is unbounded by count; only the 5s budget stops it, so it
        # must retry well past the old fixed cap of 3.
        assert attempts > 3

    @pytest.mark.asyncio
    async def test_generator_zero_budget_re_raises_after_first_attempt(
        self, fake_clock: list[float]
    ) -> None:
        attempts = 0

        @async_generator_retry(tries=None, max_elapsed_time=0.0, delay_seconds=1.0)
        async def gen() -> AsyncGenerator[int]:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("down", request=_make_request())
            yield 0  # pragma: no cover

        with pytest.raises(httpx.ConnectError):
            async for _ in gen():
                pass
        assert attempts == 1


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
        response = httpx.Response(
            status_code=429,
            headers=[(b"retry-after", b"\xb2")],
            request=httpx.Request("GET", "https://example.com"),
        )
        error = httpx.HTTPStatusError(
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

        with pytest.raises(httpx.HTTPStatusError):
            await call()
        assert sleeps == [10.0]

    @pytest.mark.asyncio
    async def test_async_retry_falls_back_to_exponential_without_header(
        self, sleeps: list[float]
    ) -> None:
        @async_retry(tries=3, delay_seconds=0.5, backoff_factor=2.0)
        async def call() -> str:
            raise _make_http_status_error(429)

        with pytest.raises(httpx.HTTPStatusError):
            await call()
        assert sleeps == [0.5, 1.05]

    @pytest.mark.asyncio
    async def test_async_generator_retry_honors_retry_after(
        self, sleeps: list[float]
    ) -> None:
        attempts = 0

        @async_generator_retry(tries=3, delay_seconds=0.5, backoff_factor=2.0)
        async def gen() -> AsyncGenerator[int]:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise _make_http_status_error(503, {"Retry-After": "4"})
            yield 1

        items = [item async for item in gen()]
        assert items == [1]
        assert sleeps == [4.0]
