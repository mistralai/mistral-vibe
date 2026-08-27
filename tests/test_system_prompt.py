from __future__ import annotations

from datetime import date
import sys

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.agents import AgentManager
from vibe.core.config import VibeConfigSchema
from vibe.core.scratchpad import init_scratchpad
from vibe.core.skills.manager import SkillManager
from vibe.core.system_prompt import get_universal_system_prompt
from vibe.core.tools import manager as tool_manager_module
from vibe.core.tools.manager import ToolManager


def _hide_standard_git_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def test_system_prompt_reports_resolved_model_when_unpinned(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    # The unpinned default (active_model == "") must still report the resolved
    # model alias, not an empty name.
    config = build_config(
        include_model_info=True,
        include_prompt_detail=False,
        include_commit_signature=False,
    )
    assert config.active_model == ""
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(config, skill_manager, agent_manager)

    assert f"Your model name is: `{config.get_active_model().alias}`" in prompt
    assert "Your model name is: ``" not in prompt


def test_get_universal_system_prompt_uses_cmd_rules_without_bash(
    monkeypatch: pytest.MonkeyPatch,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
    # No bash on PATH -> cmd.exe branch.
    monkeypatch.setattr(
        "vibe.utils.platform.shutil.which", lambda name, path=None: None
    )

    config = build_config(
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
    )
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(config, skill_manager, agent_manager)

    assert "You are Vibe, a super useful programming assistant." in prompt
    assert (
        "The operating system is Windows with shell `C:\\Windows\\System32\\cmd.exe`"
        in prompt
    )
    assert "The shell is cmd.exe, NOT bash or PowerShell" in prompt
    assert "DO NOT use Unix commands like `ls`, `grep`, `cat`" in prompt
    assert "Discard output with `2>nul`" in prompt
    assert "`&&` and `||` are valid for command chaining in cmd.exe" in prompt
    assert "Check command availability with: `where command`" in prompt
    # PowerShell is never driven by the tool, so its rules must not appear.
    assert "The shell is PowerShell, NOT bash or cmd.exe" not in prompt
    assert "Commands run through bash" not in prompt


def test_get_universal_system_prompt_uses_cmd_rules_when_comspec_is_powershell(
    monkeypatch: pytest.MonkeyPatch,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    monkeypatch.setenv(
        "COMSPEC", "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    )
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    # No bash on PATH -> explicit cmd.exe branch, regardless of COMSPEC.
    monkeypatch.setattr(
        "vibe.utils.platform.shutil.which", lambda name, path=None: None
    )

    config = build_config(
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
    )
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(config, skill_manager, agent_manager)

    assert (
        "The operating system is Windows with shell `C:\\Windows\\System32\\cmd.exe`"
        in prompt
    )
    assert "powershell.exe`" not in prompt
    assert "The shell is cmd.exe, NOT bash or PowerShell" in prompt
    assert "Discard output with `2>nul`" in prompt
    assert "Check command availability with: `where command`" in prompt


def test_get_universal_system_prompt_uses_bash_rules_when_bash_available(
    monkeypatch: pytest.MonkeyPatch,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
    # bash discovered on PATH -> bash branch.
    monkeypatch.setattr(
        "vibe.utils.platform.shutil.which",
        lambda name, path=None: (
            "C:\\Program Files\\Git\\bin\\bash.exe" if name == "bash" else None
        ),
    )

    config = build_config(
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
    )
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(config, skill_manager, agent_manager)

    assert (
        "The operating system is Windows with shell "
        "`bash (C:\\Program Files\\Git\\bin\\bash.exe)`" in prompt
    )
    assert "Commands run through bash (Git Bash)" in prompt
    assert "Discard output with `2>/dev/null`" in prompt
    assert "command -v <command>" in prompt
    # cmd.exe rules must not appear when bash is the shell.
    assert "The shell is cmd.exe, NOT bash or PowerShell" not in prompt
    assert "Discard output with `2>nul`" not in prompt


def test_get_universal_system_prompt_uses_powershell_rules_in_treatment(
    monkeypatch: pytest.MonkeyPatch,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    from vibe.core.tools.builtins import bash, git_bash, windows_shell
    from vibe.core.tools.builtins.managed_shell import backend

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(tool_manager_module, "is_windows", lambda: True)
    monkeypatch.setattr(bash, "is_windows", lambda: True)
    monkeypatch.setattr(git_bash, "is_windows", lambda: True)
    monkeypatch.setattr(git_bash, "git_bash_shell_available", lambda: False)
    monkeypatch.setattr(windows_shell, "is_windows", lambda: True)
    monkeypatch.setattr(windows_shell, "git_bash_shell_available", lambda: False)
    monkeypatch.setattr(windows_shell, "powershell_shell_available", lambda: True)
    monkeypatch.setattr(backend, "managed_shell_supported", lambda family=None: False)

    config = build_config(
        include_project_context=False,
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
        managed_shell_tools_enabled=True,
    )
    tool_manager = ToolManager(lambda: config)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(
        config, skill_manager, agent_manager, tool_manager=tool_manager
    )

    assert "The operating system is Windows with shell `PowerShell`" in prompt
    assert "The shell is PowerShell, NOT bash or cmd.exe" in prompt
    assert "Get-Command <command>" in prompt
    assert "The shell is cmd.exe, NOT bash or PowerShell" not in prompt


def test_get_universal_system_prompt_uses_git_bash_rules_in_treatment(
    monkeypatch: pytest.MonkeyPatch,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    from vibe.core.tools.builtins import bash, git_bash, windows_shell
    from vibe.core.tools.builtins.managed_shell import backend

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(tool_manager_module, "is_windows", lambda: True)
    monkeypatch.setattr(bash, "is_windows", lambda: True)
    monkeypatch.setattr(git_bash, "is_windows", lambda: True)
    monkeypatch.setattr(git_bash, "git_bash_shell_available", lambda: True)
    monkeypatch.setattr(windows_shell, "is_windows", lambda: True)
    monkeypatch.setattr(windows_shell, "git_bash_shell_available", lambda: True)
    monkeypatch.setattr(windows_shell, "powershell_shell_available", lambda: True)
    monkeypatch.setattr(backend, "is_windows", lambda: True)

    def fake_managed_shell_supported(family=None):
        return family in {"git_bash", "powershell", "windows", None}

    monkeypatch.setattr(
        backend, "managed_shell_supported", fake_managed_shell_supported
    )

    config = build_config(
        include_project_context=False,
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
        managed_shell_tools_enabled=True,
    )
    tool_manager = ToolManager(lambda: config)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(
        config, skill_manager, agent_manager, tool_manager=tool_manager
    )

    assert "The operating system is Windows with shell `Git Bash`" in prompt
    assert "Commands run through bash (Git Bash)" in prompt
    assert "Discard output with `2>/dev/null`" in prompt
    assert "The shell is PowerShell, NOT bash or cmd.exe" not in prompt


def test_scratchpad_section_included_when_passed(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    sp = init_scratchpad("test-session")
    config = build_config(
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
    )
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(
        config, skill_manager, agent_manager, scratchpad_dir=sp
    )

    assert "# Scratchpad Directory" in prompt
    assert sp is not None
    assert str(sp) in prompt


def test_scratchpad_section_absent_when_not_passed(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    config = build_config(
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
    )
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(config, skill_manager, agent_manager)

    assert "Scratchpad Directory" not in prompt


def test_headless_section_included_when_enabled(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    config = build_config(include_model_info=False, include_commit_signature=False)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(
        config, skill_manager, agent_manager, headless=True
    )

    assert "# Headless Mode" in prompt
    assert "no human is available to respond" in prompt


def test_headless_section_absent_by_default(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    config = build_config(include_model_info=False, include_commit_signature=False)
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(config, skill_manager, agent_manager)

    assert "Headless Mode" not in prompt


def test_current_date_placeholder_substituted_in_prompt(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    config = build_config(
        system_prompt_id="cli", include_model_info=False, include_commit_signature=False
    )
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(config, skill_manager, agent_manager)

    today = date.today()
    expected = f"Today's date is {today.isoformat()} ({today.strftime('%A')})."
    assert expected in prompt
    assert "$current_date" not in prompt


def test_v3_system_prompt_variant_is_available_to_legacy_harness(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    config = build_config(
        system_prompt_id="cli_2026-08_v3",
        include_model_info=False,
        include_commit_signature=False,
    )
    skill_manager = SkillManager(lambda: config)
    agent_manager = AgentManager(load_orchestrator(config))

    prompt = get_universal_system_prompt(config, skill_manager, agent_manager)

    assert prompt.startswith("You are Mistral Vibe, an interactive coding agent.")
    assert "# Harness" in prompt
    assert "invoke it via the `skill` tool" in prompt
    assert "## Instruction hierarchy" not in prompt
