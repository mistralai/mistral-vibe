from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop
from vibe.core.hooks.models import (
    HookConfig,
    HookConfigIssue,
    HookConfigResult,
    HookType,
)


@pytest.mark.asyncio
async def test_runtime_reload_rebinds_hooks_and_diagnostics(monkeypatch) -> None:
    agent_loop = build_test_agent_loop()
    hook = HookConfig(name="fresh-hook", type=HookType.PRE_TOOL, command="hook-command")
    result = HookConfigResult(
        hooks=[hook],
        issues=[HookConfigIssue(file=Path("hooks.toml"), message="warning")],
    )
    captured_harness_files = []

    def load_hooks(*, harness_files):
        captured_harness_files.append(harness_files)
        return result

    monkeypatch.setattr("vibe.core.agent_loop._loop.load_hooks_from_fs", load_hooks)

    try:
        await agent_loop.reload_with_initial_messages(reload_hooks=True)

        assert agent_loop.hooks_count == 1
        assert agent_loop.hook_config_issues == result.issues
        assert agent_loop._hooks_manager is not None
        assert agent_loop._hooks_manager.has_hooks(HookType.PRE_TOOL)
        assert agent_loop.runtime_policy.hook_config_result == result
        assert captured_harness_files == [agent_loop.harness_files]
    finally:
        await agent_loop.aclose()
