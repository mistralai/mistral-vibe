from __future__ import annotations

from functools import partial
from pathlib import Path

from jinja2 import Template
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.generic import OpenAIAdapter
from vibe.core.llm.format import normalize_messages_for_chat_template
from vibe.core.middleware import (
    PLAN_AGENT_EXIT,
    ReadOnlyAgentMiddleware,
    make_plan_agent_reminder,
)
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall
from vibe.core.utils.tags import CancellationReason, get_user_cancellation_message

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "backend" / "data"
DEVSTRAL_TEMPLATE_PATH = TEMPLATE_DIR / "devstral_chat_template.jinja"
MISTRAL_SMALL_4_TEMPLATE_PATH = TEMPLATE_DIR / "mistral_small_4_chat_template.jinja"
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


def _raise(message: str) -> None:
    raise ValueError(message)


def _render(template_path: Path, messages: list[dict]) -> str:
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.render(
        messages=messages,
        bos_token="",
        eos_token="",
        tools=[READ_FILE_TOOL],
        raise_exception=_raise,
        today="2026-09-21",
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


def _tool_result(call_id: str) -> LLMMessage:
    return LLMMessage(
        role=Role.tool, tool_call_id=call_id, name="read_file", content="file contents"
    )


_CONSECUTIVE_USERS = [
    LLMMessage(role=Role.system, content="system"),
    LLMMessage(role=Role.user, content="hello"),
    LLMMessage(role=Role.user, content="plan reminder", injected=True),
]

_TOOL_RESULT_THEN_USER = [
    LLMMessage(role=Role.system, content="system"),
    LLMMessage(role=Role.user, content="read README"),
    _assistant_tool_call("call_read"),
    _tool_result("call_read"),
    LLMMessage(role=Role.user, content="plan reminder", injected=True),
]

_MISSING_TOOL_RESPONSE_THEN_USER = [
    LLMMessage(role=Role.system, content="system"),
    LLMMessage(role=Role.user, content="run tool"),
    _assistant_tool_call("call_missing"),
    LLMMessage(role=Role.user, content="follow up", injected=True),
]

_ORPHANED_TOOL_RESULTS = [
    LLMMessage(role=Role.system, content="system"),
    LLMMessage(role=Role.user, content="run tool"),
    _tool_result("call_one"),
    _tool_result("call_two"),
    LLMMessage(role=Role.user, content="follow up", injected=True),
]

_CANCELED_CALL_MESSAGE = str(
    get_user_cancellation_message(CancellationReason.TOOL_NO_RESPONSE)
)

_BROKEN_SHAPES = [
    pytest.param(_CONSECUTIVE_USERS, "[INST]hello\n\nplan reminder", id="users"),
    pytest.param(_TOOL_RESULT_THEN_USER, "file contents", id="tool-then-user"),
    pytest.param(
        _MISSING_TOOL_RESPONSE_THEN_USER, _CANCELED_CALL_MESSAGE, id="missing-response"
    ),
]

_ORPHAN_SHAPE = pytest.param(_ORPHANED_TOOL_RESULTS, "follow up", id="orphaned")


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

    rendered = _render(
        DEVSTRAL_TEMPLATE_PATH, _to_provider_messages(list(backend_messages))
    )
    assert "hello" in rendered


def test_normalize_pairs_orphaned_tool_results() -> None:
    normalized = normalize_messages_for_chat_template(_ORPHANED_TOOL_RESULTS)

    assert [m.role for m in normalized] == [
        Role.system,
        Role.user,
        Role.assistant,
        Role.tool,
        Role.tool,
        Role.assistant,
        Role.user,
    ]
    synthesized_calls = normalized[2].tool_calls or []
    assert [tc.id for tc in synthesized_calls] == ["call_one", "call_two"]


def test_normalize_drops_empty_assistant_and_merges_adjacent() -> None:
    messages = [
        LLMMessage(role=Role.system, content="system"),
        LLMMessage(role=Role.user, content="hello"),
        LLMMessage(role=Role.assistant, content=""),
        LLMMessage(role=Role.assistant, content="there"),
        LLMMessage(role=Role.assistant, content="again"),
    ]

    normalized = normalize_messages_for_chat_template(messages)

    assert [m.role for m in normalized] == [Role.system, Role.user, Role.assistant]
    assert normalized[-1].content == "thereagain"


@pytest.mark.parametrize(("messages", "expected"), _BROKEN_SHAPES)
def test_devstral_template_rejects_broken_sequences(
    messages: list[LLMMessage], expected: str
) -> None:
    with pytest.raises(ValueError, match="conversation roles must alternate"):
        _render(DEVSTRAL_TEMPLATE_PATH, _to_provider_messages(messages))


def test_mistral_small_4_template_rejects_orphaned_tool_results() -> None:
    with pytest.raises(ValueError, match="Unexpected role 'tool' after role 'user'"):
        _render(
            MISTRAL_SMALL_4_TEMPLATE_PATH,
            _to_provider_messages(_ORPHANED_TOOL_RESULTS[:3]),
        )


def test_image_bearing_user_turns_bridge_alternates(tmp_path: Path) -> None:
    from vibe.core.types import FileImageSource, ImageAttachment

    def _attachment(alias: str) -> ImageAttachment:
        path = tmp_path / f"{alias}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return ImageAttachment(
            source=FileImageSource(path=path), alias=alias, mime_type="image/png"
        )

    first, second = _attachment("a"), _attachment("b")
    normalized = normalize_messages_for_chat_template(
        [
            LLMMessage(role=Role.user, content="first", images=[first]),
            LLMMessage(role=Role.user, content="second", images=[second]),
        ]
    )

    assert [m.role for m in normalized] == [Role.user, Role.assistant, Role.user]
    assert normalized[1].injected is True
    assert normalized[0].content == "first"
    assert normalized[2].content == "second"
    assert list(normalized[0].images or []) == [first]
    assert list(normalized[2].images or []) == [second]

    provider = _to_provider_messages(list(normalized))
    assert [m["role"] for m in provider] == ["user", "assistant", "user"]
    assert [part["type"] for part in provider[0]["content"]] == ["text", "image_url"]
    assert [part["type"] for part in provider[2]["content"]] == ["text", "image_url"]
    _render(DEVSTRAL_TEMPLATE_PATH, provider)


_ALL_SHAPES = [*_BROKEN_SHAPES, _ORPHAN_SHAPE]


@pytest.mark.parametrize(
    "render",
    [
        partial(_render, DEVSTRAL_TEMPLATE_PATH),
        partial(_render, MISTRAL_SMALL_4_TEMPLATE_PATH),
    ],
    ids=["devstral", "mistral-small-4"],
)
@pytest.mark.parametrize(("messages", "expected"), _ALL_SHAPES)
def test_templates_accept_normalized_sequences(
    messages: list[LLMMessage], expected: str, render
) -> None:
    normalized = normalize_messages_for_chat_template(messages)
    rendered = render(_to_provider_messages(normalized))
    assert expected in rendered
