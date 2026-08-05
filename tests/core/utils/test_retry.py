from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest

from vibe.core.utils.retry import (
    RetryCategory,
    RetryReason,
    _is_retryable_http_error,
    async_generator_retry,
    async_retry,
)


def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    response = httpx.Response(
        status_code=status_code, request=httpx.Request("GET", "https://example.com")
    )
    return httpx.HTTPStatusError(
        message=f"Error {status_code}", request=response.request, response=response
    )


def _make_request(url: str = "https://example.com") -> httpx.Request:
    return httpx.Request("POST", url)


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
