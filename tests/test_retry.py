"""Tests for retry functionality with rate limiting support."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest

from vibe.core.utils import (
    _get_retry_after_delay,
    _is_retryable_http_error,
    async_retry,
    async_generator_retry,
)


class TestRetryAfterParsing:
    """Tests for _get_retry_after_delay function."""

    def test_no_retry_after_header(self) -> None:
        """Should return None when no Retry-After header."""
        response = MagicMock(spec=httpx.Response)
        response.headers = {}
        error = httpx.HTTPStatusError(
            "Rate limited", request=MagicMock(), response=response
        )
        assert _get_retry_after_delay(error) is None

    def test_retry_after_seconds(self) -> None:
        """Should parse integer seconds from Retry-After header."""
        response = MagicMock(spec=httpx.Response)
        response.headers = {"retry-after": "30"}
        error = httpx.HTTPStatusError(
            "Rate limited", request=MagicMock(), response=response
        )
        assert _get_retry_after_delay(error) == 30.0

    def test_retry_after_float_seconds(self) -> None:
        """Should parse float seconds from Retry-After header."""
        response = MagicMock(spec=httpx.Response)
        response.headers = {"retry-after": "1.5"}
        error = httpx.HTTPStatusError(
            "Rate limited", request=MagicMock(), response=response
        )
        assert _get_retry_after_delay(error) == 1.5

    def test_retry_after_http_date(self) -> None:
        """Should parse HTTP-date from Retry-After header."""
        future_time = datetime.now(timezone.utc) + timedelta(seconds=60)
        http_date = future_time.strftime("%a, %d %b %Y %H:%M:%S GMT")

        response = MagicMock(spec=httpx.Response)
        response.headers = {"retry-after": http_date}
        error = httpx.HTTPStatusError(
            "Rate limited", request=MagicMock(), response=response
        )

        delay = _get_retry_after_delay(error)
        assert delay is not None
        assert 55 <= delay <= 65  # Allow some tolerance

    def test_retry_after_invalid_value(self) -> None:
        """Should return None for invalid Retry-After value."""
        response = MagicMock(spec=httpx.Response)
        response.headers = {"retry-after": "invalid"}
        error = httpx.HTTPStatusError(
            "Rate limited", request=MagicMock(), response=response
        )
        assert _get_retry_after_delay(error) is None

    def test_non_http_error(self) -> None:
        """Should return None for non-HTTP errors."""
        error = ValueError("Not an HTTP error")
        assert _get_retry_after_delay(error) is None


class TestIsRetryableHttpError:
    """Tests for _is_retryable_http_error function."""

    @pytest.mark.parametrize(
        "status_code,expected",
        [
            (408, True),  # Request Timeout
            (409, True),  # Conflict
            (425, True),  # Too Early
            (429, True),  # Too Many Requests
            (500, True),  # Internal Server Error
            (502, True),  # Bad Gateway
            (503, True),  # Service Unavailable
            (504, True),  # Gateway Timeout
            (400, False),  # Bad Request
            (401, False),  # Unauthorized
            (403, False),  # Forbidden
            (404, False),  # Not Found
            (200, False),  # OK
        ],
    )
    def test_status_codes(self, status_code: int, expected: bool) -> None:
        """Should correctly identify retryable status codes."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = status_code
        error = httpx.HTTPStatusError(
            f"Error {status_code}", request=MagicMock(), response=response
        )
        assert _is_retryable_http_error(error) == expected

    def test_non_http_error(self) -> None:
        """Should return False for non-HTTP errors."""
        assert _is_retryable_http_error(ValueError("Not HTTP")) is False


class TestAsyncRetry:
    """Tests for async_retry decorator."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self) -> None:
        """Should return result on success without retry."""
        call_count = 0

        @async_retry(tries=3)
        async def successful_func() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        result = await successful_func()
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_429(self) -> None:
        """Should retry on 429 status code."""
        call_count = 0

        @async_retry(tries=3, delay_seconds=0.01)
        async def flaky_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                response = MagicMock(spec=httpx.Response)
                response.status_code = 429
                response.headers = {}
                raise httpx.HTTPStatusError(
                    "Rate limited", request=MagicMock(), response=response
                )
            return "success"

        result = await flaky_func()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self) -> None:
        """Should raise after max retries exceeded."""
        call_count = 0

        @async_retry(tries=2, delay_seconds=0.01)
        async def always_fails() -> str:
            nonlocal call_count
            call_count += 1
            response = MagicMock(spec=httpx.Response)
            response.status_code = 429
            response.headers = {}
            raise httpx.HTTPStatusError(
                "Rate limited", request=MagicMock(), response=response
            )

        with pytest.raises(httpx.HTTPStatusError):
            await always_fails()
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_non_retryable_error_not_retried(self) -> None:
        """Should not retry non-retryable errors."""
        call_count = 0

        @async_retry(tries=3, delay_seconds=0.01)
        async def bad_request() -> str:
            nonlocal call_count
            call_count += 1
            response = MagicMock(spec=httpx.Response)
            response.status_code = 400  # Not retryable
            response.headers = {}
            raise httpx.HTTPStatusError(
                "Bad request", request=MagicMock(), response=response
            )

        with pytest.raises(httpx.HTTPStatusError):
            await bad_request()
        assert call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_respects_max_delay(self) -> None:
        """Should cap delay at max_delay."""
        sleep_calls: list[float] = []

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        call_count = 0

        @async_retry(
            tries=3,
            delay_seconds=100.0,  # Very high initial delay
            backoff_factor=2.0,
            max_delay=0.5,  # But capped at 0.5s
        )
        async def timed_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                response = MagicMock(spec=httpx.Response)
                response.status_code = 429
                response.headers = {}
                raise httpx.HTTPStatusError(
                    "Rate limited", request=MagicMock(), response=response
                )
            return "success"

        with patch("vibe.core.utils.asyncio.sleep", mock_sleep):
            result = await timed_func()

        assert result == "success"
        assert call_count == 3
        # Check that delays were capped at max_delay
        for delay in sleep_calls:
            assert delay <= 0.5

    @pytest.mark.asyncio
    async def test_respects_retry_after_header(self) -> None:
        """Should use Retry-After header value when present."""
        sleep_calls: list[float] = []

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        call_count = 0

        @async_retry(tries=3, delay_seconds=0.1, max_delay=60.0)
        async def rate_limited_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                response = MagicMock(spec=httpx.Response)
                response.status_code = 429
                response.headers = {"retry-after": "5"}
                raise httpx.HTTPStatusError(
                    "Rate limited", request=MagicMock(), response=response
                )
            return "success"

        with patch("vibe.core.utils.asyncio.sleep", mock_sleep):
            result = await rate_limited_func()

        assert result == "success"
        # The delay should be based on Retry-After (5s) plus small jitter
        assert len(sleep_calls) == 1
        assert 5.0 <= sleep_calls[0] <= 5.5


class TestAsyncGeneratorRetry:
    """Tests for async_generator_retry decorator."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self) -> None:
        """Should yield all items on success without retry."""
        call_count = 0

        @async_generator_retry(tries=3)
        async def gen_func():
            nonlocal call_count
            call_count += 1
            for i in range(3):
                yield i

        results = [item async for item in gen_func()]
        assert results == [0, 1, 2]
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_error(self) -> None:
        """Should retry generator on retryable error."""
        call_count = 0

        @async_generator_retry(tries=3, delay_seconds=0.01)
        async def flaky_gen():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                response = MagicMock(spec=httpx.Response)
                response.status_code = 429
                response.headers = {}
                raise httpx.HTTPStatusError(
                    "Rate limited", request=MagicMock(), response=response
                )
            for i in range(3):
                yield i

        results = [item async for item in flaky_gen()]
        assert results == [0, 1, 2]
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self) -> None:
        """Should raise after max retries exceeded."""
        call_count = 0

        @async_generator_retry(tries=2, delay_seconds=0.01)
        async def always_fails():
            nonlocal call_count
            call_count += 1
            response = MagicMock(spec=httpx.Response)
            response.status_code = 429
            response.headers = {}
            raise httpx.HTTPStatusError(
                "Rate limited", request=MagicMock(), response=response
            )
            yield  # Never reached

        with pytest.raises(httpx.HTTPStatusError):
            async for _ in always_fails():
                pass
        assert call_count == 2
