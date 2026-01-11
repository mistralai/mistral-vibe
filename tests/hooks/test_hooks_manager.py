"""Tests for hooks manager module."""

from __future__ import annotations

import pytest

from vibe.core.hooks.manager import (
    HookManager,
)
from vibe.core.hooks.types import (
    HookConfig,
    HookPermissionDecision,
    HooksConfig,
    HooksEventConfig,
)


class TestHookManager:
    def test_initialization(self) -> None:
        config = HooksConfig()
        manager = HookManager(
            config=config,
            session_id="test-session",
            cwd="/tmp",
        )

        assert manager.enabled is True
        assert manager.session_id == "test-session"
        assert manager.cwd == "/tmp"

    def test_disabled_hooks(self) -> None:
        config = HooksConfig(enabled=False)
        manager = HookManager(config=config)

        assert manager.enabled is False

    def test_config_getter(self) -> None:
        configs = [
            HooksConfig(enabled=True),
            HooksConfig(enabled=False),
        ]
        current_index = [0]

        def config_getter() -> HooksConfig:
            return configs[current_index[0]]

        manager = HookManager(config_getter=config_getter)

        assert manager.enabled is True

        current_index[0] = 1
        assert manager.enabled is False

    def test_update_session_info(self) -> None:
        manager = HookManager(
            config=HooksConfig(),
            session_id="old-session",
            cwd="/old/path",
        )

        manager.update_session_info("new-session", "/new/path")

        assert manager.session_id == "new-session"
        assert manager.cwd == "/new/path"


class TestHookManagerMatcher:
    def test_invalid_regex_pattern_falls_back(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                PreToolUse=[
                    HookConfig(
                        command='echo \'{}\'',
                        matcher="[invalid",  # Invalid regex
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        # Should not raise - falls back to escaped literal
        pattern = manager._get_matcher_pattern("[invalid")
        assert pattern.match("[invalid") is not None


class TestHookManagerPreToolUse:
    @pytest.mark.asyncio
    async def test_no_hooks_returns_allow(self) -> None:
        config = HooksConfig()
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_pre_tool_use("bash", {"command": "ls"})

        assert result.should_execute is True
        assert result.decision == HookPermissionDecision.ALLOW

    @pytest.mark.asyncio
    async def test_disabled_hooks_returns_allow(self) -> None:
        config = HooksConfig(
            enabled=False,
            hooks=HooksEventConfig(
                PreToolUse=[HookConfig(command="exit 1")]
            ),
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_pre_tool_use("bash", {"command": "ls"})

        assert result.should_execute is True

    @pytest.mark.asyncio
    async def test_hook_blocks_tool(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                PreToolUse=[
                    HookConfig(
                        command='echo \'{"permission_decision": "deny", "reason": "Blocked!"}\'',
                        matcher="bash",
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_pre_tool_use("bash", {"command": "rm -rf /"})

        assert result.should_execute is False
        assert result.decision == HookPermissionDecision.DENY
        assert result.reason == "Blocked!"

    @pytest.mark.asyncio
    async def test_hook_exit_code_2_blocks_tool(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                PreToolUse=[HookConfig(command="exit 2", matcher="*")]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_pre_tool_use("bash", {"command": "ls"})

        assert result.should_execute is False
        assert result.decision == HookPermissionDecision.DENY
        assert result.reason

    @pytest.mark.asyncio
    async def test_hook_matcher_filters(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                PreToolUse=[
                    HookConfig(
                        command='echo \'{"permission_decision": "deny"}\'',
                        matcher="bash",
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        bash_result = await manager.run_pre_tool_use("bash", {"command": "ls"})
        assert bash_result.should_execute is False

        read_result = await manager.run_pre_tool_use("read_file", {"path": "/tmp/file"})
        assert read_result.should_execute is True

    @pytest.mark.asyncio
    async def test_hook_matcher_regex(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                PreToolUse=[
                    HookConfig(
                        command='echo \'{"permission_decision": "deny"}\'',
                        matcher="bash|write_file",
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        bash_result = await manager.run_pre_tool_use("bash", {})
        assert bash_result.should_execute is False

        write_result = await manager.run_pre_tool_use("write_file", {})
        assert write_result.should_execute is False

        read_result = await manager.run_pre_tool_use("read_file", {})
        assert read_result.should_execute is True

    @pytest.mark.asyncio
    async def test_hook_wildcard_matcher(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                PreToolUse=[
                    HookConfig(
                        command='echo \'{"system_message": "All tools"}\'',
                        matcher="*",
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result1 = await manager.run_pre_tool_use("bash", {})
        assert len(result1.system_messages) == 1

        result2 = await manager.run_pre_tool_use("read_file", {})
        assert len(result2.system_messages) == 1

    @pytest.mark.asyncio
    async def test_hook_updates_input(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                PreToolUse=[
                    HookConfig(
                        command='echo \'{"updated_input": {"timeout": 30}}\'',
                        matcher="*",
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_pre_tool_use("bash", {"command": "ls"})

        assert result.updated_input == {"timeout": 30}

    @pytest.mark.asyncio
    async def test_hook_ask_decision(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                PreToolUse=[
                    HookConfig(
                        command='echo \'{"permission_decision": "ask"}\'',
                        matcher="*",
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_pre_tool_use("bash", {})

        assert result.should_execute is True
        assert result.decision == HookPermissionDecision.ASK


class TestHookManagerPostToolUse:
    @pytest.mark.asyncio
    async def test_no_hooks(self) -> None:
        config = HooksConfig()
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_post_tool_use("bash", {"command": "ls"}, tool_result="file.txt")

        assert result.system_messages == []

    @pytest.mark.asyncio
    async def test_hook_system_message(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                PostToolUse=[
                    HookConfig(
                        command='echo \'{"system_message": "Tool completed"}\'',
                        matcher="*",
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_post_tool_use(
            "bash", {"command": "ls"}, tool_result="file.txt"
        )

        assert "Tool completed" in result.system_messages


class TestHookManagerSessionHooks:
    @pytest.mark.asyncio
    async def test_session_start(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                SessionStart=[
                    HookConfig(
                        command='echo \'{"system_message": "Session started"}\'',
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        messages = await manager.run_session_start()

        assert "Session started" in messages

    @pytest.mark.asyncio
    async def test_session_end(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                SessionEnd=[
                    HookConfig(command="true")
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        await manager.run_session_end(message_count=10, total_tokens=5000)


class TestHookManagerUserPromptSubmit:
    @pytest.mark.asyncio
    async def test_no_hooks(self) -> None:
        config = HooksConfig()
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_user_prompt_submit("Hello!")

        assert result.should_continue is True
        assert result.modified_prompt is None

    @pytest.mark.asyncio
    async def test_hook_modifies_prompt(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                UserPromptSubmit=[
                    HookConfig(
                        command='echo \'{"modified_prompt": "Modified: Hello!"}\'',
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_user_prompt_submit("Hello!")

        assert result.modified_prompt == "Modified: Hello!"

    @pytest.mark.asyncio
    async def test_hook_blocks_prompt(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                UserPromptSubmit=[
                    HookConfig(
                        command='echo \'{"continue": false}\'',
                    )
                ]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_user_prompt_submit("Hello!")

        assert result.should_continue is False

    @pytest.mark.asyncio
    async def test_hook_exit_code_2_blocks_prompt(self) -> None:
        config = HooksConfig(
            hooks=HooksEventConfig(
                UserPromptSubmit=[HookConfig(command="exit 2")]
            )
        )
        manager = HookManager(config=config, session_id="test", cwd="/tmp")

        result = await manager.run_user_prompt_submit("Hello!")

        assert result.should_continue is False
