from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.types import LLMMessage, Role
from vibe.core.watchdog import RecoveryAdviceRequest, TiltEvaluationRequest
from vibe.core.watchdog.models import RecoveryStrategy, RunPhase


@pytest.mark.asyncio
async def test_agent_loop_runs_tool_free_accounted_tilt_evaluation() -> None:
    backend = FakeBackend(mock_llm_chunk(content='{"score": 88}'))
    agent_loop = build_test_agent_loop(backend=backend)
    agent_loop.messages.append(
        LLMMessage(role=Role.user, content="The same parser test keeps failing.")
    )

    result = await agent_loop.evaluate_watchdog_tilt(
        TiltEvaluationRequest(
            detector="repeated_call",
            phase=RunPhase.IDLE,
            evidence_count=2,
            exact_repeat_count=4,
        )
    )

    assert result.score == 88
    assert backend.requests_tools == [None]
    assert backend.requests_tool_choices == [None]
    assert backend.requests_metadata[0] is not None
    assert backend.requests_metadata[0]["call_type"] == "secondary_call"
    system_content = backend.requests_messages[0][0].content
    request_content = backend.requests_messages[0][1].content
    assert system_content is not None
    assert request_content is not None
    assert "Return exactly one JSON object" in system_content
    assert "exact_repeat_count" in request_content


@pytest.mark.asyncio
async def test_agent_loop_runs_validated_tool_free_recovery_advice() -> None:
    backend = FakeBackend(
        mock_llm_chunk(
            content=(
                '{"strategy":"rewrite_command","reason":"clear stale cache",'
                '"tool":null,"command":"pytest -q --cache-clear"}'
            )
        )
    )
    agent_loop = build_test_agent_loop(backend=backend)

    result = await agent_loop.advise_watchdog_recovery(
        RecoveryAdviceRequest(
            strategy=RecoveryStrategy.REWRITE_COMMAND,
            objective="Fix parser tests",
            detector="repeated_call",
            evidence=("same-command",),
            attempted_strategies=(RecoveryStrategy.INJECT_CONTEXT,),
        )
    )

    assert result.strategy == RecoveryStrategy.REWRITE_COMMAND
    assert result.command == "pytest -q --cache-clear"
    assert backend.requests_tools == [None]
    metadata = backend.requests_metadata[0]
    assert metadata is not None
    assert metadata["call_type"] == "secondary_call"
