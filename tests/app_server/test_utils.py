from __future__ import annotations

from vibe.app_server._utils import public_error
from vibe.app_server.models import TurnErrorCode
from vibe.core.compaction import CompactionFailedError
from vibe.core.llm.exceptions import BackendError, IncompleteStreamError, PayloadSummary


def _make_invalid_model_backend_error() -> BackendError:
    return BackendError(
        provider="test-provider",
        endpoint="/v1/chat/completions",
        status=400,
        reason="Bad Request",
        headers={},
        body_text='{"error":{"type":"invalid_model"}}',
        parsed_error=None,
        model="bad-model",
        payload_summary=PayloadSummary(
            model="bad-model",
            message_count=1,
            approx_chars=10,
            temperature=0.0,
            has_tools=False,
            tool_choice=None,
        ),
    )


def test_public_compaction_error_preserves_reason() -> None:
    error = public_error(CompactionFailedError("tool_call"))

    assert error.code == TurnErrorCode.COMPACTION_FAILED
    assert error.model_dump(mode="json")["code"] == "compaction_failed"
    assert error.details == {"reason": "tool_call"}


def test_public_incomplete_stream_error_has_distinct_code() -> None:
    error = public_error(IncompleteStreamError("provider", "model"))

    assert error.code == TurnErrorCode.INCOMPLETE_STREAM
    assert error.details == {"provider": "provider", "model": "model"}


def test_public_error_invalid_model_direct_backend_error() -> None:
    be = _make_invalid_model_backend_error()
    error = public_error(be)

    assert error.code == TurnErrorCode.INVALID_MODEL


def test_public_error_invalid_model_wrapped_runtime_error() -> None:
    be = _make_invalid_model_backend_error()
    wrapped = RuntimeError("API error: ...")
    wrapped.__cause__ = be
    error = public_error(wrapped)

    assert error.code == TurnErrorCode.INVALID_MODEL
