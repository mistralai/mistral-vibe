from __future__ import annotations

import pytest

from vibe.core.llm.exceptions import BackendError, PayloadSummary


def _make_payload_summary() -> PayloadSummary:
    return PayloadSummary(
        model="test-model",
        message_count=1,
        approx_chars=10,
        temperature=0.7,
        has_tools=False,
        tool_choice=None,
    )


def _make_error(
    *, status: int | None, headers: dict[str, str] | None = None
) -> BackendError:
    return BackendError(
        provider="test-provider",
        endpoint="/v1/chat/completions",
        status=status,
        reason="some reason",
        headers=headers or {},
        body_text="body",
        parsed_error=None,
        model="test-model",
        payload_summary=_make_payload_summary(),
    )


class TestBackendErrorFmt:
    def test_standard_status_code(self) -> None:
        err = _make_error(status=500)
        msg = str(err)
        assert "500 Internal Server Error" in msg
        assert "test-provider" in msg

    def test_non_standard_status_code(self) -> None:
        """Status 529 is not in HTTPStatus and previously raised ValueError."""
        err = _make_error(status=529)
        msg = str(err)
        assert "529" in msg
        # Should not contain a phrase since 529 is not standard
        assert "LLM backend error [test-provider]" in msg

    def test_no_status(self) -> None:
        err = _make_error(status=None)
        msg = str(err)
        assert "status: N/A" in msg

    def test_unauthorized_short_circuits(self) -> None:
        err = _make_error(status=401)
        assert str(err) == "Invalid API key. Please check your API key and try again."

    def test_rate_limit_short_circuits(self) -> None:
        err = _make_error(status=429)
        assert (
            str(err) == "Rate limit exceeded. Please wait a moment before trying again."
        )

    def test_request_id_from_headers(self) -> None:
        err = _make_error(status=500, headers={"x-request-id": "req-123"})
        assert "req-123" in str(err)

    @pytest.mark.parametrize("code", [530, 599, 999])
    def test_other_non_standard_codes(self, code: int) -> None:
        err = _make_error(status=code)
        msg = str(err)
        assert str(code) in msg
        assert "LLM backend error" in msg
