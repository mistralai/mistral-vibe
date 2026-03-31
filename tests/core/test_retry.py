from __future__ import annotations

import httpx
import pytest

from vibe.core.utils.retry import _is_retryable_http_error


def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    response = httpx.Response(
        status_code=status_code, request=httpx.Request("GET", "https://example.com")
    )
    return httpx.HTTPStatusError(
        message=f"Error {status_code}", request=response.request, response=response
    )


class TestIsRetryableHttpError:
    @pytest.mark.parametrize("code", [408, 409, 425, 429, 500, 502, 503, 504, 529])
    def test_retryable_codes(self, code: int) -> None:
        assert _is_retryable_http_error(_make_http_status_error(code)) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_non_retryable_codes(self, code: int) -> None:
        assert _is_retryable_http_error(_make_http_status_error(code)) is False

    def test_non_http_error_returns_false(self) -> None:
        assert _is_retryable_http_error(ValueError("not http")) is False

    def test_generic_exception_returns_false(self) -> None:
        assert _is_retryable_http_error(RuntimeError("boom")) is False
