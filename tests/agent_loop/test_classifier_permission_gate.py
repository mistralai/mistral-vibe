from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import cast

from pydantic import BaseModel
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent_loop import AgentLoop
from vibe.core.agent_loop._loop import (
    AUTO_MODE_MAX_CONSECUTIVE_BLOCKS,
    AUTO_MODE_MAX_TOTAL_BLOCKS,
)
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfig
from vibe.core.config.models import AutoModeConfig
from vibe.core.permissions.classifier import (
    ClassifierDecision,
    ClassifierVerdict,
    PermissionClassifier,
)
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.permissions import RequiredPermission
from vibe.core.types import (
    ApprovalResponse,
    BaseEvent,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    Role,
    ToolCall,
    ToolResultEvent,
)

TOOL_RESULT_CANARY = "CANARY_ATTACKER_CONTROLLED_TOOL_OUTPUT"
BLOCK_REASON = "This command would delete the production database"


class ClassifierCall(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    tool_name: str
    transcript: list[LLMMessage]
    required_permissions: list[RequiredPermission]


class FakeClassifier:
    def __init__(
        self, decisions: Sequence[ClassifierDecision | None], *, delay: float = 0.0
    ) -> None:
        self._decisions = list(decisions)
        self._delay = delay
        self.calls: list[ClassifierCall] = []
        self.concurrency = 0
        self.max_concurrency = 0

    async def classify(
        self,
        *,
        auto_mode: AutoModeConfig,
        tool_name: str,
        args: BaseModel,
        required_permissions: Sequence[RequiredPermission],
        transcript: Sequence[LLMMessage],
        metadata: dict[str, str] | None = None,
    ) -> ClassifierDecision | None:
        self.calls.append(
            ClassifierCall(
                tool_name=tool_name,
                transcript=list(transcript),
                required_permissions=list(required_permissions),
            )
        )
        self.concurrency += 1
        self.max_concurrency = max(self.max_concurrency, self.concurrency)
        await asyncio.sleep(self._delay)
        self.concurrency -= 1
        if not self._decisions:
            return None
        if len(self._decisions) == 1:
            return self._decisions[0]
        return self._decisions.pop(0)

    @property
    def call_count(self) -> int:
        return len(self.calls)


class ApprovalSpy:
    def __init__(self, response: ApprovalResponse = ApprovalResponse.YES) -> None:
        self._response = response
        self.calls: list[str] = []
        self.concurrency = 0
        self.max_concurrency = 0

    async def __call__(
        self,
        tool_name: str,
        args: BaseModel,
        tool_call_id: str,
        required_permissions: list[RequiredPermission] | None = None,
    ) -> tuple[ApprovalResponse, str | None]:
        self.calls.append(tool_call_id)
        self.concurrency += 1
        self.max_concurrency = max(self.max_concurrency, self.concurrency)
        await asyncio.sleep(0.01)
        self.concurrency -= 1
        return (self._response, None)

    @property
    def call_count(self) -> int:
        return len(self.calls)


class ThresholdTrippingClassifier(FakeClassifier):
    def __init__(self, decision: ClassifierDecision) -> None:
        super().__init__([decision])
        self.agent_loop: AgentLoop | None = None

    async def classify(
        self,
        *,
        auto_mode: AutoModeConfig,
        tool_name: str,
        args: BaseModel,
        required_permissions: Sequence[RequiredPermission],
        transcript: Sequence[LLMMessage],
        metadata: dict[str, str] | None = None,
    ) -> ClassifierDecision | None:
        # Simulates auto mode going away (concurrent block streak, mode toggled
        # off) while this very classification is in flight.
        if self.agent_loop is not None:
            self.agent_loop.stats.classifier_blocks_total = AUTO_MODE_MAX_TOTAL_BLOCKS
        return await super().classify(
            auto_mode=auto_mode,
            tool_name=tool_name,
            args=args,
            required_permissions=required_permissions,
            transcript=transcript,
            metadata=metadata,
        )


class ClosableClassifier:
    def __init__(self) -> None:
        self.close_count = 0

    async def aclose(self) -> None:
        self.close_count += 1


def allow(reason: str = "harmless") -> ClassifierDecision:
    return ClassifierDecision(
        effect="performs a routine operation",
        soft_deny_rule=None,
        user_authorized=False,
        scope_ok=True,
        verdict=ClassifierVerdict.ALLOW,
        reason=reason,
    )


def block(reason: str = BLOCK_REASON) -> ClassifierDecision:
    return ClassifierDecision(
        effect="deletes the production database",
        soft_deny_rule="Deploying to production or running a database migration.",
        user_authorized=False,
        scope_ok=True,
        verdict=ClassifierVerdict.BLOCK,
        reason=reason,
    )


def make_config(*, auto_mode_enabled: bool) -> VibeConfig:
    return build_test_vibe_config(
        enabled_tools=["todo"],
        tools={"todo": {"permission": ToolPermission.ASK.value}},
        auto_mode={"enabled": auto_mode_enabled},
    )


def make_todo_tool_call(call_id: str, index: int = 0) -> ToolCall:
    return ToolCall(
        id=call_id,
        index=index,
        function=FunctionCall(name="todo", arguments='{"action": "read"}'),
    )


def turns_calling_todo(count: int) -> list[list[LLMChunk]]:
    streams: list[list[LLMChunk]] = [
        [
            mock_llm_chunk(
                content=f"Turn {i}.", tool_calls=[make_todo_tool_call(f"call_{i}")]
            )
        ]
        for i in range(count)
    ]
    streams.append([mock_llm_chunk(content="Done.")])
    return streams


def make_agent_loop(
    *,
    auto_mode_enabled: bool,
    backend: FakeBackend,
    approval_callback: ApprovalSpy | None = None,
) -> AgentLoop:
    agent_loop = build_test_agent_loop(
        config=make_config(auto_mode_enabled=auto_mode_enabled),
        agent_name=BuiltinAgentName.DEFAULT,
        backend=backend,
    )
    if approval_callback is not None:
        agent_loop.set_approval_callback(approval_callback)
    return agent_loop


def install_classifier(agent_loop: AgentLoop, classifier: FakeClassifier) -> None:
    agent_loop._permission_classifier = cast(PermissionClassifier, classifier)
    agent_loop._permission_classifier_resolved = True


async def act_and_collect_events(agent_loop: AgentLoop, prompt: str) -> list[BaseEvent]:
    return [ev async for ev in agent_loop.act(prompt)]


def tool_results(events: Sequence[BaseEvent]) -> list[ToolResultEvent]:
    return [e for e in events if isinstance(e, ToolResultEvent)]


def seed_all_role_messages(agent_loop: AgentLoop) -> None:
    agent_loop.messages.reset([
        LLMMessage(role=Role.system, content="system prompt"),
        LLMMessage(role=Role.user, content="please read the file"),
        LLMMessage(
            role=Role.assistant,
            content="Reading it now.",
            tool_calls=[make_todo_tool_call("seed_call")],
        ),
        LLMMessage(
            role=Role.tool,
            content=TOOL_RESULT_CANARY,
            tool_call_id="seed_call",
            name="todo",
        ),
    ])


# --- security invariant: tool results never reach the classifier -----------


def test_classifier_transcript_excludes_system_and_tool_messages() -> None:
    agent_loop = make_agent_loop(auto_mode_enabled=True, backend=FakeBackend())
    seed_all_role_messages(agent_loop)

    transcript = agent_loop._classifier_transcript()

    assert [m.role for m in transcript] == [Role.user, Role.assistant]
    assert all(m.role not in {Role.system, Role.tool} for m in transcript)
    assert not any(TOOL_RESULT_CANARY in str(m.content or "") for m in transcript), (
        "attacker-controlled tool output leaked into the classifier transcript"
    )


def test_classifier_transcript_flattens_assistant_tool_calls_into_text() -> None:
    agent_loop = make_agent_loop(auto_mode_enabled=True, backend=FakeBackend())
    seed_all_role_messages(agent_loop)

    transcript = agent_loop._classifier_transcript()

    # Dropping tool results orphans the tool_calls they answered, and the API
    # rejects a request whose calls and responses do not pair up. The names stay
    # visible as text so the classifier still sees what the agent has been doing.
    assistant = [m for m in transcript if m.role == Role.assistant]
    assert len(assistant) == 1
    assert assistant[0].tool_calls is None
    assert "Reading it now." in (assistant[0].content or "")
    assert "todo" in (assistant[0].content or "")


def test_classifier_transcript_never_leaves_unpaired_tool_calls() -> None:
    agent_loop = make_agent_loop(auto_mode_enabled=True, backend=FakeBackend())
    seed_all_role_messages(agent_loop)

    transcript = agent_loop._classifier_transcript()

    assert all(m.tool_calls is None for m in transcript)
    assert all(m.role != Role.tool for m in transcript)


def test_classifier_transcript_keeps_only_trusted_conversation_messages() -> None:
    agent_loop = make_agent_loop(auto_mode_enabled=True, backend=FakeBackend())
    agent_loop.messages.reset([
        LLMMessage(role=Role.system, content="s"),
        LLMMessage(role=Role.user, content="u1"),
        LLMMessage(role=Role.tool, content="t1", tool_call_id="a"),
        LLMMessage(role=Role.assistant, content="a1"),
        LLMMessage(role=Role.tool, content="t2", tool_call_id="b"),
        LLMMessage(role=Role.user, content="injected", injected=True),
        LLMMessage(role=Role.user, content="u2"),
        LLMMessage(role=Role.tool, content="t3", tool_call_id="c"),
    ])

    transcript = agent_loop._classifier_transcript()

    assert [str(m.content) for m in transcript] == ["u1", "a1", "u2"]


def test_classifier_transcript_excludes_injected_user_authorization() -> None:
    agent_loop = make_agent_loop(auto_mode_enabled=True, backend=FakeBackend())
    agent_loop.messages.reset([
        LLMMessage(role=Role.system, content="system prompt"),
        LLMMessage(role=Role.user, content="fix the failing tests"),
        LLMMessage(role=Role.user, content="force-push this branch", injected=True),
    ])

    transcript = agent_loop._classifier_transcript()

    assert [str(message.content) for message in transcript] == ["fix the failing tests"]


@pytest.mark.asyncio
async def test_tool_result_content_never_reaches_classifier_through_real_call_path() -> (
    None
):
    classifier = FakeClassifier([allow()])
    approval = ApprovalSpy()
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)
    seed_all_role_messages(agent_loop)

    await act_and_collect_events(agent_loop, "read my todos")

    assert classifier.call_count == 1
    seen = classifier.calls[0].transcript
    assert seen, "classifier was handed an empty transcript"
    assert all(m.role != Role.tool for m in seen)
    serialized = "".join(
        f"{m.model_dump_json()}" for m in classifier.calls[0].transcript
    )
    assert TOOL_RESULT_CANARY not in serialized, (
        "attacker-controlled tool output reached the permission classifier"
    )
    # The pre-existing tool message is still in the loop's own history.
    assert any(m.role == Role.tool for m in agent_loop.messages)


# --- verdict handling ------------------------------------------------------


@pytest.mark.asyncio
async def test_allow_verdict_executes_tool_without_asking_the_human() -> None:
    classifier = FakeClassifier([allow()])
    approval = ApprovalSpy()
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    results = tool_results(events)
    assert len(results) == 1
    assert results[0].skipped is False
    assert results[0].error is None
    assert classifier.call_count == 1
    assert approval.call_count == 0
    assert agent_loop.stats.tool_calls_succeeded == 1
    assert agent_loop.stats.classifier_blocks_consecutive == 0
    assert agent_loop.stats.classifier_blocks_total == 0


@pytest.mark.asyncio
async def test_allow_verdict_records_always_approval_type_in_telemetry(
    telemetry_events: list[dict],
) -> None:
    classifier = FakeClassifier([allow()])
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=ApprovalSpy(),
    )
    install_classifier(agent_loop, classifier)

    await act_and_collect_events(agent_loop, "read my todos")

    finished = [
        e for e in telemetry_events if e.get("event_name") == "vibe.tool_call_finished"
    ]
    assert len(finished) == 1
    assert finished[0]["properties"]["approval_type"] == ToolPermission.ALWAYS.value


@pytest.mark.asyncio
async def test_block_verdict_skips_tool_and_surfaces_the_classifier_reason() -> None:
    classifier = FakeClassifier([block()])
    approval = ApprovalSpy()
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    results = tool_results(events)
    assert len(results) == 1
    assert results[0].skipped is True
    assert results[0].skip_reason == BLOCK_REASON
    assert results[0].cancelled is False
    assert approval.call_count == 0
    assert agent_loop.stats.tool_calls_rejected == 1
    assert agent_loop.stats.tool_calls_succeeded == 0


@pytest.mark.asyncio
async def test_block_reason_is_fed_back_to_the_model_as_the_tool_response() -> None:
    classifier = FakeClassifier([block()])
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=ApprovalSpy(),
    )
    install_classifier(agent_loop, classifier)

    await act_and_collect_events(agent_loop, "read my todos")

    tool_messages = [m for m in agent_loop.messages if m.role == Role.tool]
    assert len(tool_messages) == 1
    assert BLOCK_REASON in str(tool_messages[0].content or "")


@pytest.mark.asyncio
async def test_block_verdict_increments_both_block_counters() -> None:
    classifier = FakeClassifier([block()])
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=ApprovalSpy(),
    )
    install_classifier(agent_loop, classifier)

    await act_and_collect_events(agent_loop, "read my todos")

    assert agent_loop.stats.classifier_blocks_consecutive == 1
    assert agent_loop.stats.classifier_blocks_total == 1


@pytest.mark.asyncio
async def test_unavailable_classifier_falls_through_to_human_and_is_not_an_allow() -> (
    None
):
    classifier = FakeClassifier([None])
    approval = ApprovalSpy(response=ApprovalResponse.NO)
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    assert classifier.call_count == 1
    assert approval.call_count == 1, (
        "a None verdict must reach the human, never be treated as an allow"
    )
    results = tool_results(events)
    assert len(results) == 1
    assert results[0].skipped is True
    assert agent_loop.stats.tool_calls_succeeded == 0
    assert agent_loop.stats.classifier_blocks_consecutive == 0
    assert agent_loop.stats.classifier_blocks_total == 0


@pytest.mark.asyncio
async def test_unavailable_classifier_still_lets_the_human_approve() -> None:
    classifier = FakeClassifier([None])
    approval = ApprovalSpy(response=ApprovalResponse.YES)
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    assert approval.call_count == 1
    assert tool_results(events)[0].skipped is False
    assert agent_loop.stats.tool_calls_succeeded == 1


# --- fallback thresholds ---------------------------------------------------


@pytest.mark.asyncio
async def test_consecutive_block_limit_pauses_auto_mode_and_hands_over_to_human() -> (
    None
):
    assert AUTO_MODE_MAX_CONSECUTIVE_BLOCKS == 3
    classifier = FakeClassifier([block()])
    approval = ApprovalSpy(response=ApprovalResponse.NO)
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(AUTO_MODE_MAX_CONSECUTIVE_BLOCKS + 1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    await act_and_collect_events(agent_loop, "do risky things")

    assert classifier.call_count == AUTO_MODE_MAX_CONSECUTIVE_BLOCKS, (
        "classifier must not be consulted once auto mode has paused"
    )
    assert approval.call_count == 1
    assert agent_loop.stats.classifier_blocks_consecutive == (
        AUTO_MODE_MAX_CONSECUTIVE_BLOCKS
    )
    assert agent_loop.stats.classifier_blocks_total == AUTO_MODE_MAX_CONSECUTIVE_BLOCKS
    assert agent_loop._auto_mode_active() is False


@pytest.mark.asyncio
async def test_allow_resets_the_consecutive_block_counter() -> None:
    classifier = FakeClassifier([block(), block(), allow(), block(), block()])
    approval = ApprovalSpy()
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(5)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    await act_and_collect_events(agent_loop, "mixed bag")

    assert classifier.call_count == 5
    assert approval.call_count == 0, (
        "an ALLOW between blocks must reset the streak so the limit never trips"
    )
    assert agent_loop.stats.classifier_blocks_consecutive == 2
    assert agent_loop.stats.classifier_blocks_total == 4
    assert agent_loop._auto_mode_active() is True


@pytest.mark.asyncio
async def test_total_block_limit_pauses_auto_mode() -> None:
    assert AUTO_MODE_MAX_TOTAL_BLOCKS == 20
    classifier = FakeClassifier([block()])
    approval = ApprovalSpy(response=ApprovalResponse.NO)
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)
    agent_loop.stats.classifier_blocks_total = AUTO_MODE_MAX_TOTAL_BLOCKS

    await act_and_collect_events(agent_loop, "one more risky thing")

    assert agent_loop._auto_mode_active() is False
    assert classifier.call_count == 0
    assert approval.call_count == 1


@pytest.mark.asyncio
async def test_total_block_limit_is_not_tripped_one_block_early() -> None:
    classifier = FakeClassifier([block()])
    approval = ApprovalSpy(response=ApprovalResponse.NO)
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)
    agent_loop.stats.classifier_blocks_total = AUTO_MODE_MAX_TOTAL_BLOCKS - 1

    await act_and_collect_events(agent_loop, "one more risky thing")

    assert classifier.call_count == 1
    assert approval.call_count == 0
    assert agent_loop.stats.classifier_blocks_total == AUTO_MODE_MAX_TOTAL_BLOCKS


@pytest.mark.asyncio
async def test_block_counters_are_not_reset_between_turns() -> None:
    classifier = FakeClassifier([block()])
    approval = ApprovalSpy()
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1) + turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    await act_and_collect_events(agent_loop, "first turn")
    assert agent_loop.stats.classifier_blocks_consecutive == 1
    assert agent_loop.stats.classifier_blocks_total == 1

    await act_and_collect_events(agent_loop, "second turn")

    assert agent_loop.stats.classifier_blocks_consecutive == 2
    assert agent_loop.stats.classifier_blocks_total == 2


# --- mode gating -----------------------------------------------------------


@pytest.mark.asyncio
async def test_classifier_is_never_consulted_when_auto_mode_is_disabled() -> None:
    classifier = FakeClassifier([allow()])
    approval = ApprovalSpy(response=ApprovalResponse.YES)
    agent_loop = make_agent_loop(
        auto_mode_enabled=False,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    assert classifier.call_count == 0
    assert approval.call_count == 1
    assert tool_results(events)[0].skipped is False


@pytest.mark.asyncio
async def test_auto_mode_disabled_leaves_rejection_path_unchanged() -> None:
    classifier = FakeClassifier([allow()])
    approval = ApprovalSpy(response=ApprovalResponse.NO)
    agent_loop = make_agent_loop(
        auto_mode_enabled=False,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    assert classifier.call_count == 0
    assert tool_results(events)[0].skipped is True
    assert agent_loop.stats.tool_calls_rejected == 1


@pytest.mark.asyncio
async def test_no_classifier_is_constructed_when_auto_mode_never_activates() -> None:
    approval = ApprovalSpy(response=ApprovalResponse.YES)
    agent_loop = make_agent_loop(
        auto_mode_enabled=False,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    assert agent_loop._permission_classifier_resolved is False
    assert agent_loop._permission_classifier is None

    await act_and_collect_events(agent_loop, "read my todos")

    assert agent_loop._permission_classifier_resolved is False, (
        "ADR-0009: a session that never enters auto mode must never build a classifier"
    )
    assert agent_loop._permission_classifier is None


@pytest.mark.asyncio
async def test_paused_auto_mode_does_not_construct_a_classifier_either() -> None:
    approval = ApprovalSpy(response=ApprovalResponse.YES)
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    agent_loop.stats.classifier_blocks_total = AUTO_MODE_MAX_TOTAL_BLOCKS

    await act_and_collect_events(agent_loop, "read my todos")

    assert agent_loop._permission_classifier_resolved is False
    assert approval.call_count == 1


# --- headless / programmatic ----------------------------------------------


@pytest.mark.asyncio
async def test_headless_block_returns_the_classifier_reason_not_the_generic_denial() -> (
    None
):
    classifier = FakeClassifier([block()])
    agent_loop = make_agent_loop(
        auto_mode_enabled=True, backend=FakeBackend(turns_calling_todo(1))
    )
    assert agent_loop.approval_callback is None
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    results = tool_results(events)
    assert len(results) == 1
    assert results[0].skipped is True
    assert results[0].skip_reason == BLOCK_REASON
    assert results[0].skip_reason != "Tool execution not permitted."


@pytest.mark.asyncio
async def test_headless_unavailable_classifier_falls_back_to_generic_denial() -> None:
    classifier = FakeClassifier([None])
    agent_loop = make_agent_loop(
        auto_mode_enabled=True, backend=FakeBackend(turns_calling_todo(1))
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    results = tool_results(events)
    assert results[0].skipped is True
    assert results[0].skip_reason == "Tool execution not permitted."


@pytest.mark.asyncio
async def test_headless_allow_executes_without_any_approval_callback() -> None:
    classifier = FakeClassifier([allow()])
    agent_loop = make_agent_loop(
        auto_mode_enabled=True, backend=FakeBackend(turns_calling_todo(1))
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    assert tool_results(events)[0].skipped is False
    assert agent_loop.stats.tool_calls_succeeded == 1


# --- concurrency -----------------------------------------------------------


@pytest.mark.asyncio
async def test_human_approvals_stay_serialized_when_classifier_returns_none() -> None:
    classifier = FakeClassifier([None], delay=0.01)
    approval = ApprovalSpy(response=ApprovalResponse.YES)
    parallel_calls = [make_todo_tool_call(f"call_p{i}", index=i) for i in range(3)]
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend([
            [mock_llm_chunk(content="Three tools.", tool_calls=parallel_calls)],
            [mock_llm_chunk(content="All done.")],
        ]),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    await act_and_collect_events(agent_loop, "Go")

    assert classifier.call_count == 3
    assert approval.call_count == 3
    assert classifier.max_concurrency > 1, (
        "the classifier call must run with the permission lock released, "
        "otherwise this test cannot prove serialization survives it"
    )
    assert approval.max_concurrency == 1, (
        "releasing the permission lock for the classifier call must not break "
        "serialization of human approvals"
    )
    assert agent_loop.stats.tool_calls_agreed == 3
    assert agent_loop.stats.tool_calls_succeeded == 3


@pytest.mark.asyncio
async def test_parallel_allow_verdicts_all_execute_without_human_approval() -> None:
    classifier = FakeClassifier([allow()], delay=0.01)
    approval = ApprovalSpy()
    parallel_calls = [make_todo_tool_call(f"call_a{i}", index=i) for i in range(3)]
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend([
            [mock_llm_chunk(content="Three tools.", tool_calls=parallel_calls)],
            [mock_llm_chunk(content="All done.")],
        ]),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "Go")

    assert classifier.call_count == 3
    assert approval.call_count == 0
    assert classifier.max_concurrency > 1
    assert all(r.skipped is False for r in tool_results(events))
    assert agent_loop.stats.tool_calls_succeeded == 3


# --- what the classifier is handed ----------------------------------------


@pytest.mark.asyncio
async def test_classifier_receives_the_pending_tool_name_and_permissions() -> None:
    classifier = FakeClassifier([allow()])
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=ApprovalSpy(),
    )
    install_classifier(agent_loop, classifier)

    await act_and_collect_events(agent_loop, "read my todos")

    assert classifier.call_count == 1
    call = classifier.calls[0]
    assert call.tool_name == "todo"
    assert isinstance(call.required_permissions, list)
    user_messages = [m for m in call.transcript if m.role == Role.user]
    assert any("read my todos" in str(m.content or "") for m in user_messages)


@pytest.mark.asyncio
async def test_bypassed_permissions_skip_the_classifier_entirely() -> None:
    classifier = FakeClassifier([block()])
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(
            enabled_tools=["todo"],
            tools={"todo": {"permission": ToolPermission.ASK.value}},
            auto_mode={"enabled": True},
        ),
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        backend=FakeBackend(turns_calling_todo(1)),
    )
    install_classifier(agent_loop, classifier)

    events = await act_and_collect_events(agent_loop, "read my todos")

    assert classifier.call_count == 0
    assert tool_results(events)[0].skipped is False


def test_auto_builtin_agent_profile_enables_auto_mode() -> None:
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(enabled_tools=["todo"]),
        agent_name=BuiltinAgentName.AUTO,
        backend=FakeBackend(),
    )

    assert agent_loop.config.auto_mode.enabled is True
    assert agent_loop._auto_mode_active() is True


def test_default_builtin_agent_profile_leaves_auto_mode_off() -> None:
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(enabled_tools=["todo"]),
        agent_name=BuiltinAgentName.DEFAULT,
        backend=FakeBackend(),
    )

    assert agent_loop.config.auto_mode.enabled is False
    assert agent_loop._auto_mode_active() is False


# --- verdicts that land after auto mode went away --------------------------


@pytest.mark.asyncio
async def test_allow_arriving_after_auto_mode_paused_is_not_auto_executed() -> None:
    classifier = ThresholdTrippingClassifier(allow())
    approval = ApprovalSpy(response=ApprovalResponse.YES)
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)
    classifier.agent_loop = agent_loop

    events = await act_and_collect_events(agent_loop, "read my todos")

    assert classifier.call_count == 1
    assert agent_loop._auto_mode_active() is False
    assert approval.call_count == 1, (
        "a stale ALLOW must not bypass the human once auto mode has gone away"
    )
    assert tool_results(events)[0].skipped is False


@pytest.mark.asyncio
async def test_block_arriving_after_auto_mode_paused_falls_through_to_approval() -> (
    None
):
    classifier = ThresholdTrippingClassifier(block())
    approval = ApprovalSpy(response=ApprovalResponse.NO)
    agent_loop = make_agent_loop(
        auto_mode_enabled=True,
        backend=FakeBackend(turns_calling_todo(1)),
        approval_callback=approval,
    )
    install_classifier(agent_loop, classifier)
    classifier.agent_loop = agent_loop

    events = await act_and_collect_events(agent_loop, "read my todos")

    assert classifier.call_count == 1
    assert approval.call_count == 1
    results = tool_results(events)
    assert results[0].skipped is True
    assert results[0].skip_reason != BLOCK_REASON, (
        "a stale BLOCK must not be applied; the human decides instead"
    )
    # The stale verdict must not move the counters either.
    assert agent_loop.stats.classifier_blocks_consecutive == 0
    assert agent_loop.stats.classifier_blocks_total == AUTO_MODE_MAX_TOTAL_BLOCKS


# --- classifier lifecycle --------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_config_discards_and_closes_the_cached_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    classifier = ClosableClassifier()
    agent_loop = make_agent_loop(auto_mode_enabled=True, backend=FakeBackend())
    agent_loop._permission_classifier = cast(PermissionClassifier, classifier)
    agent_loop._permission_classifier_resolved = True

    monkeypatch.setattr(
        VibeConfig, "load", staticmethod(lambda: make_config(auto_mode_enabled=True))
    )
    await agent_loop.refresh_config()

    # The classifier captures its model and provider at build time, so a config
    # change has to drop it for the next call to rebuild.
    assert agent_loop._permission_classifier is None
    assert agent_loop._permission_classifier_resolved is False
    assert classifier.close_count == 1


@pytest.mark.asyncio
async def test_aclose_closes_the_cached_classifier() -> None:
    classifier = ClosableClassifier()
    agent_loop = make_agent_loop(auto_mode_enabled=True, backend=FakeBackend())
    agent_loop._permission_classifier = cast(PermissionClassifier, classifier)
    agent_loop._permission_classifier_resolved = True

    await agent_loop.aclose()

    assert agent_loop._permission_classifier is None
    assert agent_loop._permission_classifier_resolved is False
    assert classifier.close_count == 1
