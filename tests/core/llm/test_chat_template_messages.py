from __future__ import annotations

from pathlib import Path

from jinja2 import Template
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.generic import OpenAIAdapter
from vibe.core.llm.format import (
    normalize_messages_for_chat_template,
    roles_satisfy_chat_template_alternation,
)
from vibe.core.middleware import (
    PLAN_AGENT_EXIT,
    ReadOnlyAgentMiddleware,
    make_plan_agent_reminder,
)
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall

DEVSTRAL_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "data"
    / "devstral_chat_template.jinja"
)
READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _to_provider_messages(messages: list[LLMMessage]) -> list[dict]:
    provider = ProviderConfig(
        name="llamacpp", api_base="http://127.0.0.1:11434/v1", api_key_env_var="API_KEY"
    )
    return OpenAIAdapter()._convert_messages(messages, provider)


def _render_devstral_template(messages: list[dict]) -> str:
    template = Template(DEVSTRAL_TEMPLATE_PATH.read_text(encoding="utf-8"))

    def raise_exception(message: str) -> None:
        raise ValueError(message)

    return template.render(
        messages=messages,
        bos_token="",
        eos_token="",
        tools=[READ_FILE_TOOL],
        raise_exception=raise_exception,
    )


def _assistant_tool_call(call_id: str, *, name: str = "read_file") -> LLMMessage:
    return LLMMessage(
        role=Role.assistant,
        content="",
        tool_calls=[
            ToolCall(
                id=call_id,
                index=0,
                function=FunctionCall(
                    name=name, arguments='{"file_path": "README.md"}'
                ),
            )
        ],
    )


def test_roles_fail_on_consecutive_user_messages() -> None:
    messages = [
        LLMMessage(role=Role.system, content="system"),
        LLMMessage(role=Role.user, content="hello"),
        LLMMessage(role=Role.user, content="plan reminder", injected=True),
    ]

    assert roles_satisfy_chat_template_alternation(messages) is False


def test_normalize_merges_consecutive_user_messages() -> None:
    messages = [
        LLMMessage(role=Role.system, content="system"),
        LLMMessage(role=Role.user, content="hello"),
        LLMMessage(role=Role.user, content="plan reminder", injected=True),
    ]

    normalized = normalize_messages_for_chat_template(messages)

    assert roles_satisfy_chat_template_alternation(normalized) is True
    assert [message.role for message in normalized] == [Role.system, Role.user]
    assert "hello" in (normalized[1].content or "")
    assert "plan reminder" in (normalized[1].content or "")


def test_normalize_bridges_user_after_tool_results() -> None:
    messages = [
        LLMMessage(role=Role.system, content="system"),
        LLMMessage(role=Role.user, content="read README"),
        _assistant_tool_call("call_read"),
        LLMMessage(
            role=Role.tool,
            tool_call_id="call_read",
            name="read_file",
            content="file contents",
        ),
        LLMMessage(role=Role.user, content="plan reminder", injected=True),
    ]

    assert roles_satisfy_chat_template_alternation(messages) is False

    normalized = normalize_messages_for_chat_template(messages)

    assert roles_satisfy_chat_template_alternation(normalized) is True
    assert [message.role for message in normalized] == [
        Role.system,
        Role.user,
        Role.assistant,
        Role.tool,
        Role.assistant,
        Role.user,
    ]


def test_normalize_fills_missing_tool_responses_before_user() -> None:
    messages = [
        LLMMessage(role=Role.system, content="system"),
        LLMMessage(role=Role.user, content="run tool"),
        _assistant_tool_call("call_missing"),
        LLMMessage(role=Role.user, content="follow up", injected=True),
    ]

    assert roles_satisfy_chat_template_alternation(messages) is False

    normalized = normalize_messages_for_chat_template(messages)

    assert roles_satisfy_chat_template_alternation(normalized) is True
    tool_messages = [message for message in normalized if message.role == Role.tool]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call_missing"


def test_normalize_drops_empty_assistant_messages() -> None:
    messages = [
        LLMMessage(role=Role.system, content="system"),
        LLMMessage(role=Role.user, content="hello"),
        LLMMessage(role=Role.assistant, content=""),
        LLMMessage(role=Role.assistant, content="there"),
    ]

    normalized = normalize_messages_for_chat_template(messages)

    assert roles_satisfy_chat_template_alternation(normalized) is True
    assert normalized[-1].content == "there"


@pytest.mark.asyncio
async def test_plan_agent_middleware_injection_normalizes_for_backend() -> None:
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(
            include_model_info=False, include_commit_signature=False, enabled_tools=[]
        ),
        agent_name=BuiltinAgentName.PLAN,
    )
    agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))

    plan_middleware = ReadOnlyAgentMiddleware(
        lambda: agent_loop.agent_profile,
        BuiltinAgentName.PLAN,
        lambda: make_plan_agent_reminder(
            agent_loop._plan_session.plan_file_path_str,
            has_ask_user_question=False,
            has_exit_plan_mode=False,
        ),
        PLAN_AGENT_EXIT,
    )

    result = await plan_middleware.before_turn(agent_loop._get_context())
    async for _event in agent_loop._handle_middleware_result(result):
        pass

    raw_roles = [message.role for message in agent_loop.messages]
    assert raw_roles.count(Role.user) >= 2

    backend_messages = agent_loop._messages_for_backend(
        agent_loop.messages, agent_loop.config.get_active_model()
    )

    assert roles_satisfy_chat_template_alternation(backend_messages) is True


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param(
            [
                LLMMessage(role=Role.system, content="system"),
                LLMMessage(role=Role.user, content="hello"),
                LLMMessage(role=Role.user, content="plan reminder", injected=True),
            ],
            id="middleware-consecutive-users",
        ),
        pytest.param(
            [
                LLMMessage(role=Role.system, content="system"),
                LLMMessage(role=Role.user, content="read README"),
                _assistant_tool_call("call_read"),
                LLMMessage(
                    role=Role.tool,
                    tool_call_id="call_read",
                    name="read_file",
                    content="file contents",
                ),
                LLMMessage(role=Role.user, content="plan reminder", injected=True),
            ],
            id="tool-results-then-injected-user",
        ),
        pytest.param(
            [
                LLMMessage(role=Role.system, content="system"),
                LLMMessage(role=Role.user, content="run tool"),
                _assistant_tool_call("call_missing"),
                LLMMessage(role=Role.user, content="follow up", injected=True),
            ],
            id="missing-tool-response-then-user",
        ),
    ],
)
def test_devstral_template_rejects_broken_sequences(messages: list[LLMMessage]) -> None:
    with pytest.raises(ValueError, match="conversation roles must alternate"):
        _render_devstral_template(_to_provider_messages(messages))


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param(
            [
                LLMMessage(role=Role.system, content="system"),
                LLMMessage(role=Role.user, content="hello"),
                LLMMessage(role=Role.user, content="plan reminder", injected=True),
            ],
            id="middleware-consecutive-users",
        ),
        pytest.param(
            [
                LLMMessage(role=Role.system, content="system"),
                LLMMessage(role=Role.user, content="read README"),
                _assistant_tool_call("call_read"),
                LLMMessage(
                    role=Role.tool,
                    tool_call_id="call_read",
                    name="read_file",
                    content="file contents",
                ),
                LLMMessage(role=Role.user, content="plan reminder", injected=True),
            ],
            id="tool-results-then-injected-user",
        ),
        pytest.param(
            [
                LLMMessage(role=Role.system, content="system"),
                LLMMessage(role=Role.user, content="run tool"),
                _assistant_tool_call("call_missing"),
                LLMMessage(role=Role.user, content="follow up", injected=True),
            ],
            id="missing-tool-response-then-user",
        ),
    ],
)
def test_devstral_template_accepts_normalized_sequences(
    messages: list[LLMMessage],
) -> None:
    normalized = normalize_messages_for_chat_template(messages)
    rendered = _render_devstral_template(_to_provider_messages(normalized))
    assert rendered
