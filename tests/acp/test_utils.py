from __future__ import annotations

from acp.schema import ToolCallStart

from vibe.acp.tools.builtins.task import Task as AcpTask
from vibe.acp.tools.builtins.todo import Todo as AcpTodo, TodoArgs
from vibe.acp.user_display_content import USER_DISPLAY_CONTENT_META_KEY
from vibe.acp.utils import (
    TOOL_OPTIONS,
    ToolOption,
    build_permission_options,
    create_tool_call_replay,
    create_tool_result_replay,
    create_user_message_replay,
    get_proxy_help_text,
    tool_call_replay_update,
)
from vibe.core.llm.format import ResolvedToolCall
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.proxy_setup import SUPPORTED_PROXY_VARS
from vibe.core.tools.builtins.task import TaskArgs
from vibe.core.tools.permissions import PermissionScope, RequiredPermission
from vibe.core.types import (
    FunctionCall,
    LLMMessage,
    Role,
    ToolCall,
    UserDisplayContentMetadata,
)


def _write_env_file(content: str) -> None:
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_ENV_FILE.path.write_text(content, encoding="utf-8")


class TestGetProxyHelpText:
    def test_returns_string(self) -> None:
        result = get_proxy_help_text()

        assert isinstance(result, str)

    def test_includes_proxy_configuration_header(self) -> None:
        result = get_proxy_help_text()

        assert "## Proxy Configuration" in result

    def test_includes_usage_section(self) -> None:
        result = get_proxy_help_text()

        assert "### Usage:" in result
        assert "/proxy-setup" in result

    def test_includes_all_supported_variables(self) -> None:
        result = get_proxy_help_text()

        for key in SUPPORTED_PROXY_VARS:
            assert f"`{key}`" in result

    def test_shows_none_configured_when_no_settings(self) -> None:
        result = get_proxy_help_text()

        assert "(none configured)" in result

    def test_shows_current_settings_when_configured(self) -> None:
        _write_env_file("HTTP_PROXY=http://proxy:8080\n")

        result = get_proxy_help_text()

        assert "HTTP_PROXY=http://proxy:8080" in result
        assert "(none configured)" not in result

    def test_shows_only_set_values(self) -> None:
        _write_env_file("HTTP_PROXY=http://proxy:8080\n")

        result = get_proxy_help_text()

        assert "HTTP_PROXY=http://proxy:8080" in result
        assert "HTTPS_PROXY=" not in result


class TestBuildPermissionOptions:
    def test_no_permissions_returns_default_options(self) -> None:
        result = build_permission_options(None)
        assert result is TOOL_OPTIONS

    def test_empty_list_returns_default_options(self) -> None:
        result = build_permission_options([])
        assert result is TOOL_OPTIONS

    def test_with_permissions_includes_labels_in_allow_always(self) -> None:
        permissions = [
            RequiredPermission(
                scope=PermissionScope.COMMAND_PATTERN,
                invocation_pattern="npm install foo",
                session_pattern="npm install *",
                label="npm install *",
            )
        ]
        result = build_permission_options(permissions)

        assert len(result) == 4
        allow_always = next(o for o in result if o.option_id == ToolOption.ALLOW_ALWAYS)
        assert "session" in allow_always.name.lower()
        allow_permanent = next(
            o for o in result if o.option_id == ToolOption.ALLOW_ALWAYS_PERMANENT
        )
        assert "Always allow" == allow_permanent.name

    def test_allow_always_has_field_meta(self) -> None:
        permissions = [
            RequiredPermission(
                scope=PermissionScope.COMMAND_PATTERN,
                invocation_pattern="mkdir foo",
                session_pattern="mkdir *",
                label="mkdir *",
            )
        ]
        result = build_permission_options(permissions)

        allow_always = next(o for o in result if o.option_id == ToolOption.ALLOW_ALWAYS)
        assert allow_always.field_meta is not None
        assert "required_permissions" in allow_always.field_meta
        meta_perms = allow_always.field_meta["required_permissions"]
        assert len(meta_perms) == 1
        assert meta_perms[0]["session_pattern"] == "mkdir *"

    def test_allow_once_and_reject_unchanged(self) -> None:
        permissions = [
            RequiredPermission(
                scope=PermissionScope.URL_PATTERN,
                invocation_pattern="example.com",
                session_pattern="example.com",
                label="fetching from example.com",
            )
        ]
        result = build_permission_options(permissions)

        allow_once = next(o for o in result if o.option_id == ToolOption.ALLOW_ONCE)
        reject_once = next(o for o in result if o.option_id == ToolOption.REJECT_ONCE)
        assert allow_once.name == "Allow once"
        assert reject_once.name == "Deny"
        assert allow_once.field_meta is None
        assert reject_once.field_meta is None


class TestCreateUserMessageReplay:
    def test_replays_plain_text_without_field_meta(self) -> None:
        replay = create_user_message_replay(
            LLMMessage(role=Role.user, content="Hello", message_id="msg-1")
        )

        assert replay.content.text == "Hello"
        assert replay.message_id == "msg-1"
        assert replay.field_meta is None

    def test_replays_user_display_content_as_field_meta(self) -> None:
        user_display_content = UserDisplayContentMetadata(
            version="1.0.0",
            host="mistral-vscode",
            content=[
                {"type": "text", "text": "Look at "},
                {
                    "type": "workspace_mention",
                    "kind": "file",
                    "uri": "file:///repo/src/app.ts",
                    "name": "app.ts",
                },
            ],
        )

        replay = create_user_message_replay(
            LLMMessage(
                role=Role.user,
                content="Look at app.ts",
                message_id="msg-1",
                user_display_content=user_display_content,
            )
        )

        assert replay.content.text == "Look at app.ts"
        assert replay.field_meta == {
            USER_DISPLAY_CONTENT_META_KEY: user_display_content.model_dump(mode="json")
        }


class TestCreateToolCallReplay:
    def test_carries_status_and_resolved_kind(self) -> None:
        update = create_tool_call_replay("call_1", "grep", '{"pattern": "foo"}')

        assert update.status == "completed"
        assert update.kind == "search"
        assert update.field_meta == {"tool_name": "grep"}
        assert update.raw_input == '{"pattern": "foo"}'

    def test_unknown_tool_defaults_kind_to_other(self) -> None:
        update = create_tool_call_replay("call_2", "mystery_tool", None)

        assert update.status == "completed"
        assert update.kind == "other"
        assert update.field_meta == {"tool_name": "mystery_tool"}


class TestCreateToolResultReplay:
    def test_carries_kind_and_tool_name_meta(self) -> None:
        msg = LLMMessage(
            role=Role.tool, tool_call_id="call_1", name="bash", content="exit 0"
        )

        update = create_tool_result_replay(msg)

        assert update is not None
        assert update.kind == "execute"
        assert update.status == "completed"
        assert update.field_meta == {"tool_name": "bash"}

    def test_returns_none_without_tool_call_id(self) -> None:
        msg = LLMMessage(role=Role.tool, name="bash", content="exit 0")

        assert create_tool_result_replay(msg) is None


def _tool_call(call_id: str, name: str, arguments: str | None) -> ToolCall:
    return ToolCall(id=call_id, function=FunctionCall(name=name, arguments=arguments))


class TestToolCallReplayUpdate:
    def test_resolved_task_carries_agent_and_task_meta(self) -> None:
        tool_call = _tool_call(
            "call_1", "task", '{"agent": "explore", "task": "find the bug"}'
        )
        resolved = ResolvedToolCall(
            tool_name="task",
            tool_class=AcpTask,
            validated_args=TaskArgs(agent="explore", task="find the bug"),
            call_id="call_1",
        )

        update = tool_call_replay_update(resolved, tool_call)

        assert isinstance(update, ToolCallStart)
        assert update.status == "completed"
        assert update.field_meta is not None
        assert update.field_meta["tool_name"] == "task"
        assert update.field_meta["agent"] == "explore"
        assert update.field_meta["task"] == "find the bug"

    def test_unresolved_call_falls_back_to_generic_replay(self) -> None:
        tool_call = _tool_call("call_2", "grep", '{"pattern": "foo"}')

        update = tool_call_replay_update(None, tool_call)

        assert isinstance(update, ToolCallStart)
        assert update.status == "completed"
        assert update.kind == "search"
        assert update.field_meta == {"tool_name": "grep"}

    def test_hidden_tool_call_is_skipped(self) -> None:
        tool_call = _tool_call("call_3", "todo", "{}")
        resolved = ResolvedToolCall(
            tool_name="todo",
            tool_class=AcpTodo,
            validated_args=TodoArgs(action="read"),
            call_id="call_3",
        )

        assert tool_call_replay_update(resolved, tool_call) is None
