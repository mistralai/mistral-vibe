from __future__ import annotations

from vibe.app_server._utils import public_error
from vibe.app_server.models import TurnErrorCode
from vibe.core.compaction import CompactionFailedError


def test_public_compaction_error_preserves_reason() -> None:
    error = public_error(CompactionFailedError("tool_call"))

    assert error.code == TurnErrorCode.COMPACTION_FAILED
    assert error.model_dump(mode="json")["code"] == "compaction_failed"
    assert error.details == {"reason": "tool_call"}
