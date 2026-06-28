from __future__ import annotations

import json
import shlex
import sys
import textwrap
from typing import Any

import pytest

from tests.agent_loop.e2e.conftest import MistralAPI, build_e2e_agent_loop
from tests.backend.data.mistral import mistral_completion
from tests.conftest import build_test_vibe_config
from vibe.core.hooks.config import HookConfigResult
from vibe.core.hooks.models import HookConfig, HookType
from vibe.core.types import AssistantEvent, ToolResultEvent

TODO_TOOL_CALL: list[dict[str, Any]] = [
    {
        "id": "call_todo",
        "function": {"name": "todo", "arguments": '{"action": "read"}'},
        "index": 0,
    }
]


def _emit_cmd(payload: dict[str, Any]) -> str:
    # Payload is passed via stdin of the runner (stdout of parent process)
    body = f"import sys; sys.stdout.write({json.dumps(payload)!r})"
    return f"{sys.executable} -c {shlex.quote(body)}"


@pytest.mark.asyncio
async def test_before_tool_hook_denies_tool_and_skips_execution(
    mistral_api: MistralAPI,
) -> None:
    deny_hook = HookConfig(
        name="deny",
        type=HookType.BEFORE_TOOL,
        command=_emit_cmd({"decision": "deny", "reason": "blocked by policy"}),
        match="todo",
    )
    mistral_api.reply(
        mistral_completion("", tool_calls=TODO_TOOL_CALL), mistral_completion("done")
    )
    agent = build_e2e_agent_loop(
        config=build_test_vibe_config(enabled_tools=["todo"]),
        hook_config_result=HookConfigResult(hooks=[deny_hook], issues=[]),
    )

    events = [event async for event in agent.act("Show my todos")]

    tool_result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert tool_result.skipped is True
    assert "blocked by policy" in (tool_result.skip_reason or "")


@pytest.mark.asyncio
async def test_before_tool_hook_rewrites_tool_input_seen_by_model(
    mistral_api: MistralAPI,
) -> None:
    rewritten = {
        "action": "write",
        "todos": [{"id": "1", "content": "rewritten by hook"}],
    }
    rewrite_hook = HookConfig(
        name="rewrite",
        type=HookType.BEFORE_TOOL,
        command=_emit_cmd({"hook_specific_output": {"tool_input": rewritten}}),
        match="todo",
    )
    mistral_api.reply(
        mistral_completion("", tool_calls=TODO_TOOL_CALL), mistral_completion("done")
    )
    agent = build_e2e_agent_loop(
        config=build_test_vibe_config(enabled_tools=["todo"]),
        hook_config_result=HookConfigResult(hooks=[rewrite_hook], issues=[]),
    )

    [_ async for _ in agent.act("Update my todos")]

    assert "rewritten by hook" in mistral_api.model_facing_text(1)


@pytest.mark.asyncio
async def test_before_tool_rewrite_failing_validation_is_denied(
    mistral_api: MistralAPI,
) -> None:
    bad_rewrite_hook = HookConfig(
        name="bad-rewrite",
        type=HookType.BEFORE_TOOL,
        command=_emit_cmd({"hook_specific_output": {"tool_input": {"todos": "x"}}}),
        match="todo",
    )
    mistral_api.reply(
        mistral_completion("", tool_calls=TODO_TOOL_CALL), mistral_completion("done")
    )
    agent = build_e2e_agent_loop(
        config=build_test_vibe_config(enabled_tools=["todo"]),
        hook_config_result=HookConfigResult(hooks=[bad_rewrite_hook], issues=[]),
    )

    events = [event async for event in agent.act("Update my todos")]

    tool_result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert tool_result.skipped is True
    assert "failed validation" in (tool_result.skip_reason or "")


@pytest.mark.asyncio
async def test_after_tool_hook_replaces_tool_output_seen_by_model(
    mistral_api: MistralAPI,
) -> None:
    replace_output_hook = HookConfig(
        name="replace",
        type=HookType.AFTER_TOOL,
        command=_emit_cmd({"decision": "deny", "reason": "REPLACED OUTPUT"}),
        match="todo",
    )
    mistral_api.reply(
        mistral_completion("", tool_calls=TODO_TOOL_CALL), mistral_completion("done")
    )
    agent = build_e2e_agent_loop(
        config=build_test_vibe_config(enabled_tools=["todo"]),
        hook_config_result=HookConfigResult(hooks=[replace_output_hook], issues=[]),
    )

    [_ async for _ in agent.act("Show my todos")]

    assert "REPLACED OUTPUT" in mistral_api.model_facing_text(1)


@pytest.mark.asyncio
async def test_post_agent_turn_hook_injects_retry_user_message(
    mistral_api: MistralAPI,
) -> None:
    # A post-agent-turn hook that denies forever would loop, so this script
    # denies only on its first run, using a sentinel file to stay silent after.
    deny_decision = json.dumps({"decision": "deny", "reason": "please continue"})
    deny_once = textwrap.dedent(f"""
        import os, sys
        sentinel = "__post_turn_sentinel__"
        if not os.path.exists(sentinel):
            open(sentinel, "w").close()
            sys.stdout.write({deny_decision!r})
    """)
    retry_hook = HookConfig(
        name="retry",
        type=HookType.POST_AGENT_TURN,
        command=f"{sys.executable} -c {shlex.quote(deny_once)}",
    )
    mistral_api.reply(
        mistral_completion("first answer"), mistral_completion("second answer")
    )
    agent = build_e2e_agent_loop(
        config=build_test_vibe_config(enabled_tools=["todo"]),
        hook_config_result=HookConfigResult(hooks=[retry_hook], issues=[]),
    )

    events = [event async for event in agent.act("Hello")]

    assert "please continue" in mistral_api.model_facing_text(1)
    answers = [e.content for e in events if isinstance(e, AssistantEvent)]
    assert answers == ["first answer", "second answer"]
