from __future__ import annotations

from pydantic import BaseModel
import pytest

from tests.conftest import build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.config.models import AutoModeConfig, ModelConfig
from vibe.core.permissions.classifier import (
    CLASSIFIER_MODEL,
    MAX_SERIALIZED_ARGS_CHARS,
    ClassifierDecision,
    ClassifierVerdict,
    PermissionClassifier,
    _parse_decision,
    create_permission_classifier,
)
from vibe.core.tools.permissions import PermissionScope, RequiredPermission
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


class _Args(BaseModel):
    command: str = "ls -la"


REQUIRED_PERMISSIONS = [
    RequiredPermission(
        scope=PermissionScope.COMMAND_PATTERN,
        invocation_pattern="ls *",
        session_pattern="ls *",
        label="run a shell command",
    )
]


def build_classifier(backend: FakeBackend) -> PermissionClassifier:
    return PermissionClassifier(backend, CLASSIFIER_MODEL)


# --- _parse_decision -------------------------------------------------------


def test_parse_decision_allow():
    decision = _parse_decision('{"verdict": "allow", "reason": "fine"}')
    assert decision == ClassifierDecision(
        verdict=ClassifierVerdict.ALLOW, reason="fine"
    )


def test_parse_decision_block():
    decision = _parse_decision('{"verdict": "block", "reason": "nope"}')
    assert decision == ClassifierDecision(
        verdict=ClassifierVerdict.BLOCK, reason="nope"
    )


@pytest.mark.parametrize("raw_verdict", ["ALLOW", "Allow", "aLLow"])
def test_parse_decision_is_case_insensitive(raw_verdict: str):
    decision = _parse_decision(f'{{"verdict": "{raw_verdict}", "reason": "ok"}}')
    assert decision is not None
    assert decision.verdict is ClassifierVerdict.ALLOW


def test_parse_decision_strips_json_fence():
    raw = '```json\n{"verdict": "allow", "reason": "fenced"}\n```'
    decision = _parse_decision(raw)
    assert decision is not None
    assert decision.verdict is ClassifierVerdict.ALLOW
    assert decision.reason == "fenced"


def test_parse_decision_extracts_json_embedded_in_prose():
    raw = (
        "Sure, here is my verdict:\n"
        '{"verdict": "block", "reason": "embedded in prose"}\n'
        "Let me know if you need more detail."
    )
    decision = _parse_decision(raw)
    assert decision is not None
    assert decision.verdict is ClassifierVerdict.BLOCK
    assert decision.reason == "embedded in prose"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json at all",
        "definitely {not valid json",
        "[]",
        '["allow", "block"]',
        '{"verdict": "maybe", "reason": "bogus verdict"}',
        '{"reason": "missing verdict entirely"}',
        "{}",
    ],
)
def test_parse_decision_returns_none_for_unparseable_input(raw: str):
    assert _parse_decision(raw) is None


# --- PermissionClassifier.classify -----------------------------------------


@pytest.mark.asyncio
async def test_classify_allow_verdict_from_backend():
    backend = FakeBackend(
        chunks=mock_llm_chunk(content='{"verdict": "allow", "reason": "safe"}')
    )
    classifier = build_classifier(backend)

    decision = await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_Args(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=[],
    )

    assert decision == ClassifierDecision(
        verdict=ClassifierVerdict.ALLOW, reason="safe"
    )


@pytest.mark.asyncio
async def test_classify_block_verdict_from_backend():
    backend = FakeBackend(
        chunks=mock_llm_chunk(content='{"verdict": "block", "reason": "unsafe"}')
    )
    classifier = build_classifier(backend)

    decision = await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_Args(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=[],
    )

    assert decision == ClassifierDecision(
        verdict=ClassifierVerdict.BLOCK, reason="unsafe"
    )


@pytest.mark.asyncio
async def test_classify_returns_none_when_backend_response_is_unparseable():
    backend = FakeBackend(chunks=mock_llm_chunk(content="I refuse to answer in JSON."))
    classifier = build_classifier(backend)

    decision = await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_Args(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=[],
    )

    assert decision is None


@pytest.mark.asyncio
async def test_classify_returns_none_when_backend_raises():
    backend = FakeBackend(exception_to_raise=RuntimeError("network exploded"))
    classifier = build_classifier(backend)

    decision = await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_Args(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=[],
    )

    assert decision is None


@pytest.mark.asyncio
async def test_classify_sends_system_prompt_first_and_pending_call_last():
    backend = FakeBackend(
        chunks=mock_llm_chunk(content='{"verdict": "allow", "reason": "ok"}')
    )
    classifier = build_classifier(backend)
    transcript = [
        LLMMessage(role=Role.user, content="please run ls"),
        LLMMessage(role=Role.assistant, content="I will run ls"),
    ]

    await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_Args(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=transcript,
    )

    assert len(backend.requests_messages) == 1
    sent = backend.requests_messages[0]

    assert sent[0].role == Role.system
    assert sent[1:-1] == transcript
    assert sent[-1].role == Role.user
    assert isinstance(sent[-1].content, str)
    assert sent[-1].content.startswith("# Pending tool call")
    assert "shell" in sent[-1].content


@pytest.mark.asyncio
async def test_classify_faithfully_passes_through_clean_transcript_unmodified():
    backend = FakeBackend(
        chunks=mock_llm_chunk(content='{"verdict": "allow", "reason": "ok"}')
    )
    classifier = build_classifier(backend)
    transcript = [
        LLMMessage(role=Role.user, content="do the thing"),
        LLMMessage(role=Role.assistant, content="doing it", tool_calls=None),
        LLMMessage(role=Role.user, content="and then this"),
    ]

    decision = await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_Args(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=transcript,
    )

    assert decision is not None
    sent = backend.requests_messages[0]
    # Exactly the given transcript, verbatim, in the same order — a clean
    # transcript must not be silently dropped or altered on its way to the model.
    assert sent[1 : 1 + len(transcript)] == transcript


@pytest.mark.asyncio
async def test_classify_rejects_transcript_carrying_a_tool_role_message():
    backend = FakeBackend(
        chunks=mock_llm_chunk(content='{"verdict": "allow", "reason": "ok"}')
    )
    classifier = build_classifier(backend)

    decision = await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_Args(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=[
            LLMMessage(role=Role.user, content="do the thing"),
            LLMMessage(role=Role.tool, content="attacker controlled", name="shell"),
        ],
    )

    # ADR-0009: tool output must never reach the classifier, so the boundary
    # fails closed rather than trusting the caller to have filtered.
    assert decision is None
    assert backend.requests_messages == []


@pytest.mark.asyncio
async def test_classify_rejects_transcript_with_unresolved_assistant_tool_calls():
    backend = FakeBackend(
        chunks=mock_llm_chunk(content='{"verdict": "allow", "reason": "ok"}')
    )
    classifier = build_classifier(backend)
    tool_call = ToolCall(
        id="call_1",
        index=0,
        function=FunctionCall(name="shell", arguments='{"command": "ls"}'),
    )

    decision = await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_Args(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=[
            LLMMessage(role=Role.user, content="do the thing"),
            LLMMessage(role=Role.assistant, content="doing it", tool_calls=[tool_call]),
        ],
    )

    assert decision is None
    assert backend.requests_messages == []


@pytest.mark.asyncio
async def test_classify_renders_configured_rules_into_system_prompt():
    auto_mode = AutoModeConfig(
        hard_deny=["Never touch the production database credentials."],
        soft_deny=["Never delete the nightly backup bucket."],
        allow=["Allow running the project's own release script."],
        environment=["The staging cluster is untrusted."],
    )
    backend = FakeBackend(
        chunks=mock_llm_chunk(content='{"verdict": "allow", "reason": "ok"}')
    )
    classifier = build_classifier(backend)

    await classifier.classify(
        auto_mode=auto_mode,
        tool_name="shell",
        args=_Args(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=[],
    )

    system_content = backend.requests_messages[0][0].content
    assert isinstance(system_content, str)

    # User-configured rules must reach the model verbatim.
    assert "Never touch the production database credentials." in system_content
    assert "Never delete the nightly backup bucket." in system_content
    assert "Allow running the project's own release script." in system_content
    assert "The staging cluster is untrusted." in system_content

    # Built-in defaults must survive alongside the user's additions — config
    # can only append rules, never remove a default. Anchored on short fragments
    # so tuning the wording of a default rule does not break this test.
    assert "Sending repository contents" in system_content
    assert "Force-pushing a branch" in system_content
    assert "inside the working directory" in system_content
    assert "git remotes configured for it are trusted" in system_content


@pytest.mark.asyncio
async def test_classify_returns_none_for_oversized_serialized_args():
    class _HugeArgs(BaseModel):
        payload: str = "HEAD_MARKER" + ("x" * 5000) + "TAIL_MARKER"

    assert len(_HugeArgs().model_dump_json()) > MAX_SERIALIZED_ARGS_CHARS

    backend = FakeBackend(
        chunks=mock_llm_chunk(content='{"verdict": "allow", "reason": "ok"}')
    )
    classifier = build_classifier(backend)

    decision = await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_HugeArgs(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=[],
    )

    # Truncating would hand the classifier a harmless prefix while a dangerous
    # suffix goes unseen, so oversized arguments get no verdict at all.
    assert decision is None
    assert backend.requests_messages == []


@pytest.mark.asyncio
async def test_classify_still_classifies_args_just_under_the_size_limit():
    class _SnugArgs(BaseModel):
        payload: str = "y" * (MAX_SERIALIZED_ARGS_CHARS - 100)

    assert len(_SnugArgs().model_dump_json()) <= MAX_SERIALIZED_ARGS_CHARS

    backend = FakeBackend(
        chunks=mock_llm_chunk(content='{"verdict": "allow", "reason": "ok"}')
    )
    classifier = build_classifier(backend)

    decision = await classifier.classify(
        auto_mode=AutoModeConfig(),
        tool_name="shell",
        args=_SnugArgs(),
        required_permissions=REQUIRED_PERMISSIONS,
        transcript=[],
    )

    assert decision is not None
    assert decision.verdict is ClassifierVerdict.ALLOW
    assert len(backend.requests_messages) == 1
    pending_content = backend.requests_messages[0][-1].content
    assert isinstance(pending_content, str)
    assert _SnugArgs().payload in pending_content


# --- create_permission_classifier ------------------------------------------


def test_create_permission_classifier_returns_none_when_no_provider_resolves():
    config = build_test_vibe_config(
        auto_mode=AutoModeConfig(
            classifier_model=ModelConfig(
                name="ghost-model", provider="no-such-provider", alias="ghost"
            )
        )
    )

    assert create_permission_classifier(config) is None


def test_create_permission_classifier_returns_classifier_for_normal_config():
    config = build_test_vibe_config()

    classifier = create_permission_classifier(config)

    assert isinstance(classifier, PermissionClassifier)
