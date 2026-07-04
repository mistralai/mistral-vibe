from __future__ import annotations

from datetime import date
import sys

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.agents import AgentManager
from vibe.core.scratchpad import init_scratchpad
from vibe.core.skills.manager import SkillManager
from vibe.core.system_prompt import get_universal_system_prompt
from vibe.core.tools.manager import ToolManager


def test_get_universal_system_prompt_includes_windows_prompt_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
    # Mock get_shell_executable to return cmd.exe (no bash available) to test cmd.exe fallback
    # Need to mock is_windows and get_shell_executable at the utils module level
    from vibe.core import system_prompt, utils
    from vibe.core.utils import platform as platform_module

    monkeypatch.setattr(platform_module, "is_windows", lambda: True)
    monkeypatch.setattr(
        utils,
        "get_shell_executable",
        lambda config=None: "C:\\Windows\\System32\\cmd.exe",
    )
    # Also need to update the reference in system_prompt module
    monkeypatch.setattr(
        system_prompt,
        "get_shell_executable",
        lambda config=None: "C:\\Windows\\System32\\cmd.exe",
    )

    config = build_test_vibe_config(
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
    )
    tool_manager = ToolManager(lambda: config)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(lambda: config)

    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager
    )

    assert "You are Vibe, a super useful programming assistant." in prompt
    assert (
        "The operating system is Windows with shell `C:\\Windows\\System32\\cmd.exe`"
        in prompt
    )
    assert (
        "DO NOT use Unix commands like `ls`, `grep`, `cat` - they won't work on Windows"
    ) in prompt
    assert "Use: `dir` (Windows) for directory listings" in prompt
    assert "Use: backslashes (\\\\) for paths" in prompt
    assert "Check command availability with: `where command` (Windows)" in prompt
    assert "Script shebang: Not applicable on Windows" in prompt


def test_system_prompt_respects_vibe_shell_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    shell_executable = "C:\\custom\\bash.exe"
    monkeypatch.setenv("VIBE_SHELL", shell_executable)
    # Need to mock is_windows to return True since we're testing Windows behavior
    from pathlib import Path
    from unittest.mock import patch

    from vibe.core.utils import platform as platform_module

    monkeypatch.setattr(platform_module, "is_windows", lambda: True)
    # Mock Path.exists to return True for the custom bash path
    with patch.object(Path, "exists", return_value=True):
        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            include_prompt_detail=True,
            include_model_info=False,
            include_commit_signature=False,
        )
        tool_manager = ToolManager(lambda: config)
        skill_manager = SkillManager(lambda: config)
        agent_manager = AgentManager(lambda: config)

        prompt = get_universal_system_prompt(
            tool_manager, config, skill_manager, agent_manager
        )

        assert "You are Vibe, a super useful programming assistant." in prompt
        assert (
            "The operating system is Windows with shell `C:\\custom\\bash.exe`"
            in prompt
        )
        assert (
            f"- {shell_executable} is available ('Git binaries / Mingw64 / Cygwin')"
            in prompt
        )
        assert "Unix commands like `ls`, `grep`, `cat` work" in prompt
        assert "- **Use forward slashes (`/`) in paths** for bash\n" in prompt
        assert (
            "- **Do not** use Windows utilities or Powershell commands like `dir`, `findstr`, `Get-ChildItem`, `Select-String`, `Get-Content`\n"
            in prompt
        )


def test_scratchpad_section_included_when_passed() -> None:
    sp = init_scratchpad("test-session")
    config = build_test_vibe_config(
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
    )
    tool_manager = ToolManager(lambda: config)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(lambda: config)

    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager, scratchpad_dir=sp
    )

    assert "# Scratchpad Directory" in prompt
    assert sp is not None
    assert str(sp) in prompt


def test_scratchpad_section_absent_when_not_passed() -> None:
    config = build_test_vibe_config(
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
    )
    tool_manager = ToolManager(lambda: config)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(lambda: config)

    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager
    )

    assert "Scratchpad Directory" not in prompt


def test_headless_section_included_when_enabled() -> None:
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    tool_manager = ToolManager(lambda: config)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(lambda: config)

    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager, headless=True
    )

    assert "# Headless Mode" in prompt
    assert "no human is available to respond" in prompt


def test_headless_section_absent_by_default() -> None:
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    tool_manager = ToolManager(lambda: config)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(lambda: config)

    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager
    )

    assert "Headless Mode" not in prompt


def test_current_date_placeholder_substituted_in_prompt() -> None:
    config = build_test_vibe_config(
        system_prompt_id="cli", include_model_info=False, include_commit_signature=False
    )
    tool_manager = ToolManager(lambda: config)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(lambda: config)

    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager
    )

    today = date.today()
    expected = f"Today's date is {today.isoformat()} ({today.strftime('%A')})."
    assert expected in prompt
    assert "$current_date" not in prompt
