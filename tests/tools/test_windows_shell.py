from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from vibe.core.tools.base import BaseToolState, ToolPermission
from vibe.core.tools.builtins import (
    bash as bash_module,
    experimental_bash as experimental_bash_module,
    windows_shell as windows_shell_module,
)
from vibe.core.tools.builtins.experimental_bash import (
    ExperimentalBashArgs,
    ExperimentalBashToolConfig,
)
from vibe.core.tools.builtins.git_bash import (
    ExperimentalGitBash,
    GitBash,
    GitBashArgs,
    GitBashToolConfig,
)
from vibe.core.tools.builtins.managed_shell import _windows
from vibe.core.tools.builtins.windows_shell import (
    ExperimentalWindowsShell,
    WindowsShell,
    WindowsShellArgs,
    WindowsShellToolConfig,
)
from vibe.core.tools.permissions import PermissionContext
from vibe.core.utils import is_windows
from vibe.core.workspace import Workspace
from vibe.permissions import PathGrantScope, PermissionScope, path_grant_pattern
from vibe.utils import paths


def test_windows_shell_resolver_prefers_pwsh_then_powershell(monkeypatch):
    calls: list[str] = []
    resolved = {"powershell.exe": "C:/Windows/System32/powershell.exe"}

    def fake_resolve(candidate: str) -> str | None:
        calls.append(candidate)
        return resolved.get(candidate)

    monkeypatch.setattr(_windows, "_resolve_executable", fake_resolve)

    spec = _windows.resolve_windows_shell_spec(None, None)

    assert spec.executable == "C:/Windows/System32/powershell.exe"
    assert spec.family == "powershell"
    assert calls == ["pwsh.exe", "powershell.exe"]


def test_git_bash_normalizes_msys_drive_paths_before_workdir_check(
    tmp_path, monkeypatch
):
    # Compared as paths, not strings: str(WindowsPath) renders backslashes.
    seen_paths: list[Path] = []
    inside = {tmp_path, Path("C:/repo/file.txt")}

    def is_within_workdir(path: str, **_kwargs) -> bool:
        seen_paths.append(Path(path))
        return Path(path) in inside

    monkeypatch.setattr(experimental_bash_module, "is_windows", lambda: True)
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    monkeypatch.setattr(
        experimental_bash_module, "is_path_within_workdir", is_within_workdir
    )

    outside_dirs = experimental_bash_module._collect_outside_dirs(
        ["cat /c/repo/file.txt"],
        command_cwd=tmp_path,
        workspace=Workspace.for_session(tmp_path),
        scratchpad_dir=None,
    )

    assert outside_dirs == set()
    assert Path("C:/repo/file.txt") in seen_paths


def test_windows_shell_resolver_rejects_cmd_override(monkeypatch):
    monkeypatch.setattr(
        _windows, "_resolve_executable", lambda candidate: "C:/Windows/System32/cmd.exe"
    )

    with pytest.raises(
        _windows.ManagedShellBackendError, match="PowerShell shell override"
    ):
        _windows.resolve_windows_shell_spec("cmd.exe", None)


def test_git_bash_resolver_uses_windows_bash_path(monkeypatch):
    monkeypatch.setattr(
        _windows, "get_windows_bash_path", lambda: "C:/Program Files/Git/bin/bash.exe"
    )

    spec = _windows.resolve_git_bash_shell_spec(None, None)

    assert spec.executable == "C:/Program Files/Git/bin/bash.exe"
    assert spec.family == "bash"


def test_git_bash_resolver_rejects_powershell_override(monkeypatch):
    monkeypatch.setattr(
        _windows,
        "_resolve_executable",
        lambda candidate: "C:/Program Files/PowerShell/7/pwsh.exe",
    )

    with pytest.raises(_windows.ManagedShellBackendError, match="Git Bash"):
        _windows.resolve_git_bash_shell_spec("pwsh.exe", None)


def test_pywinpty_availability_handles_missing_import(monkeypatch):
    real_import_module = importlib.import_module

    def fake_import_module(name: str):
        if name == "winpty":
            raise ImportError("missing")
        return real_import_module(name)

    monkeypatch.setattr(_windows.importlib, "import_module", fake_import_module)

    assert _windows.pywinpty_available() is False


def test_windows_shell_permission_requires_approval_for_outside_cwd(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["dir"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="dir", cwd=str(outside))
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_nested_commands_are_all_allowlisted():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo", "get-date"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command="echo $(Get-Date)"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


def test_windows_shell_nested_denylisted_command_is_rejected():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            allowlist=["echo"], denylist=["remove-item"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"echo $(Remove-Item C:\outside\secret.txt)")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "Remove-Item" in (permission.reason or "")


@pytest.mark.parametrize(
    "command",
    [
        "echo 'a`'; Remove-Item x",
        "echo a^; Remove-Item x",
        "echo 'x’; Remove-Item x; ‘y'",
        "echo 'a'#it's\nRemove-Item x",
        "echo a$(echo ')'; Remove-Item x)",
        "echo \"$(echo ')'; Remove-Item x)\"",
        "echo a\x1c#; Remove-Item x",
        "echo '--%' it's | Remove-Item x",
        "echo -a:--%; Remove-Item x",
        "echo a, --% b; Remove-Item x",
        "echo a,\n--% b; Remove-Item x",
    ],
)
def test_windows_shell_denylist_sees_every_statement_powershell_runs(command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            allowlist=["echo"], denylist=["remove-item"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER


def test_windows_shell_hashtable_values_require_approval():
    # A hashtable value is a pipeline that PowerShell runs. It is checked as
    # "a = Remove-Item x", which no allowlist entry matches.
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="echo @{a = Remove-Item x}")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize(
    "command_template",
    [
        "type x#y {outside}",
        'type `"x {outside} `"',
        "type x,{outside}",
        'type "x"{outside}',
        "type 'x’ {outside} '",
        "type `u{{2E}}`u{{2E}}/outside/secret.txt",
        r"Get-Content -Path.\..\outside\secret.txt",
        "type (echo {outside})",
        "Get-Content -Path:(echo {outside})",
        "echo hi > (echo {outside})",
    ],
)
def test_windows_shell_path_check_sees_the_arguments_powershell_passes(
    command_template, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside" / "secret.txt"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            allowlist=["echo", "type", "get-content"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=command_template.format(outside=outside.as_posix()))
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize(
    ("pattern", "command"),
    [
        ("remove-item", "rm victim.txt"),
        ("remove-item", "del victim.txt"),
        ("remove-item", "erase victim.txt"),
        ("remove-item", "ri victim.txt"),
        ("remove-item", "rd victim.txt"),
        ("get-content", "gc file.txt"),
        ("get-content", "cat file.txt"),
        ("get-content", "type file.txt"),
        ("get-childitem", "gci ."),
        ("get-childitem", "dir ."),
        ("get-childitem", "ls ."),
    ],
)
def test_windows_shell_denylist_resolves_powershell_aliases(pattern, command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=[pattern]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER


@pytest.mark.parametrize(
    ("pattern", "command"),
    [
        ("rm", "Remove-Item victim.txt"),
        ("gc", "Get-Content file.txt"),
        ("gci", "Get-ChildItem ."),
    ],
)
def test_windows_shell_denylist_canonicalizes_alias_patterns(pattern, command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=[pattern]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER


def test_windows_shell_denylist_resolves_alias_after_call_operator():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=["remove-item"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command="& rm victim.txt"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER


def test_windows_shell_alias_expansion_does_not_apply_to_explicit_executable():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=["remove-item"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r".\tools\rm.exe victim.txt")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


def test_windows_shell_always_still_asks_for_an_executable_outside_the_workdir():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(permission=ToolPermission.ALWAYS),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"C:\tools\rm.exe victim.txt")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert [required.scope for required in permission.required_permissions] == [
        PermissionScope.OUTSIDE_DIRECTORY
    ]


@pytest.mark.parametrize(
    ("pattern", "command"),
    [("get-content", "gc file.txt"), ("gc", "Get-Content file.txt")],
)
def test_windows_shell_allowlist_resolves_powershell_aliases(pattern, command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[pattern]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


def test_windows_shell_sensitive_pattern_resolves_powershell_alias():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, sensitive_patterns=["remove-item"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command="rm victim.txt"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("command", ["& { cmd /k }", "& (cmd /k)"])
def test_windows_shell_wrapped_denylisted_command_is_rejected(command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=["cmd /k"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "cmd /k" in (permission.reason or "")


def test_windows_shell_nested_unknown_command_requires_permission():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="echo $(Remove-Item secret.txt)")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.label == "Remove-Item *"
        for required in permission.required_permissions
    )


def test_windows_shell_nested_allowlisted_command_checks_outside_path(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    outside_file = outside / "secret.txt"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo", "remove-item"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=f'echo $(Remove-Item "{outside_file}")')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_single_ampersand_chain_requires_permission():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["dir"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="dir & del secrets.txt")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.label == "del *" for required in permission.required_permissions
    )


def test_windows_shell_file_command_requires_approval_for_outside_path(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    outside_file = outside / "secret.txt"
    workdir.mkdir()
    outside.mkdir()
    outside_file.write_text("secret", encoding="utf-8")
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["type"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=f'type "{outside_file}"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.invocation_pattern == str(outside_file.resolve())
        for required in permission.required_permissions
    )


def test_windows_shell_bare_name_linking_outside_requires_approval(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside_file = tmp_path / "secret.txt"
    workdir.mkdir()
    outside_file.write_text("secret", encoding="utf-8")
    try:
        (workdir / "lnk").symlink_to(outside_file)
    except OSError:
        pytest.skip("this host cannot create symlinks")
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["Get-Content"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command="Get-Content lnk"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.invocation_pattern == str(outside_file.resolve())
        for required in permission.required_permissions
    )


def test_windows_shell_honors_an_exact_persisted_outside_path_grant(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    approved_file = outside / "approved.txt"
    sibling_file = outside / "sibling.txt"
    workdir.mkdir()
    outside.mkdir()
    approved_file.write_text("approved", encoding="utf-8")
    sibling_file.write_text("sibling", encoding="utf-8")
    monkeypatch.chdir(workdir)
    approved_pattern = path_grant_pattern(
        str(approved_file.resolve()), PathGrantScope.EXACT
    )
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            allowlist=["type", approved_pattern]
        ),
        state=BaseToolState(),
    )

    approved = tool.resolve_permission(
        WindowsShellArgs(command=f'type "{approved_file}"')
    )
    sibling = tool.resolve_permission(
        WindowsShellArgs(command=f'type "{sibling_file}"')
    )

    assert isinstance(approved, PermissionContext)
    assert approved.permission is ToolPermission.ALWAYS
    assert isinstance(sibling, PermissionContext)
    assert sibling.permission is ToolPermission.ASK


def test_windows_shell_path_qualified_executable_does_not_match_basename_allowlist():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["tree"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=r".\tree.exe"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_windows_shell_path_qualified_executable_checks_outside_path():
    executable = r"C:\Temp\tree.exe"
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[executable]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=executable))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        r"C:\Temp" in required.label for required in permission.required_permissions
    )


def test_windows_shell_attached_path_parameter_requires_approval():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["get-content"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"Get-Content -Path:C:\outside\secret.txt")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        r"C:\outside" in required.label for required in permission.required_permissions
    )


def test_windows_shell_expands_powershell_home_before_path_check(tmp_path, monkeypatch):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("USERPROFILE", str(outside))
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["type"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r'type "$HOME\secret.txt"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_expands_braced_environment_names_with_special_characters(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("ProgramFiles(x86)", str(outside))
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["get-content"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r'Get-Content "${env:ProgramFiles(x86)}\secret.txt"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_unsupported_braced_variable_requires_approval(monkeypatch):
    monkeypatch.setenv("destination", "inside")
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["get-content"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r'Get-Content "${global:destination}\secret.txt"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.label == r"dynamic PowerShell path (${global:destination}\secret.txt)"
        for required in permission.required_permissions
    )


def test_windows_shell_redirection_requires_approval_for_outside_path():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"echo pwned > C:\outside\profile.ps1")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        r"C:\outside" in required.label for required in permission.required_permissions
    )


@pytest.mark.parametrize(
    "command",
    [
        "echo pwned > output.txt",
        "echo pwned >> output.txt",
        "echo pwned *> output.txt",
        "echo pwned 2> errors.txt",
    ],
)
def test_windows_shell_redirection_requires_approval_inside_workspace(command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.label.startswith("output redirection (")
        for required in permission.required_permissions
    )


@pytest.mark.parametrize(
    "command",
    [
        "echo harmless > $null",
        "echo harmless > NUL",
        "echo harmless > NUL:",
        "echo harmless 2>&1",
        "echo harmless *>&1",
    ],
)
def test_windows_shell_discard_and_descriptor_redirections_remain_allowed(command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


def test_windows_shell_quoted_redirection_text_is_not_treated_as_a_target():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r'echo "not > C:\outside\profile.ps1"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


@pytest.mark.parametrize(
    ("pattern", "command"),
    [
        ("git diff", r"git diff --no-index C:\outside\a.txt C:\outside\b.txt"),
        ("get-content", r"Get-Content FileSystem::C:\outside\secret.txt"),
        ("out-file", r"Out-File -FilePath C:\outside\secret.txt"),
    ],
)
def test_windows_shell_all_allowlisted_commands_check_explicit_outside_paths(
    pattern, command
):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[pattern]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        r"C:\outside" in required.label for required in permission.required_permissions
    )


@pytest.mark.parametrize(
    "command",
    [
        r"Get-Content Registry::HKEY_LOCAL_MACHINE\Software",
        "Get-Content /outside/secret.txt",
    ],
)
def test_windows_shell_ambiguous_provider_and_root_paths_require_approval(command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["get-content"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_windows_shell_allowlisted_command_checks_dynamic_path(tmp_path, monkeypatch):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("USERPROFILE", str(outside))
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["out-file"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="Out-File -FilePath $HOME")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


@pytest.mark.parametrize(
    ("pattern", "command_template"),
    [
        ("get-content", 'Get-Content "FileSystem::{inside}"'),
        ("out-file", 'Out-File -FilePath "{inside}"'),
        ("tree", "tree /f"),
    ],
)
def test_windows_shell_allowlisted_command_keeps_in_workdir_paths_allowed(
    pattern, command_template, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    command = command_template.format(inside=workdir / "inside.txt")
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[pattern]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


@pytest.mark.parametrize("command", ["git diff", "git log", "git status"])
def test_windows_shell_git_readers_remain_allowed_for_an_ordinary_repository(
    command, tmp_path, monkeypatch
):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
    monkeypatch.chdir(tmp_path)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[command]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


@pytest.mark.parametrize(
    "command",
    [
        "git log --remerge-diff -1",
        "git log -p --diff-merges=remerge",
        "git log -p --diff-merges=r",
    ],
)
def test_windows_shell_git_remerge_diff_requires_approval(
    command, tmp_path, monkeypatch
):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
    monkeypatch.chdir(tmp_path)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["git log"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_windows_shell_repeated_git_reader_checks_each_repository(
    tmp_path, monkeypatch
):
    clean = tmp_path / "clean"
    evil = clean / "evil"
    for repository in (tmp_path, clean, evil):
        (repository / ".git").mkdir(parents=True)
        (repository / ".git" / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
        )
    (evil / ".git" / "config").write_text("[diff]\n\texternal = evil-diff.exe\n")
    monkeypatch.chdir(tmp_path)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["cd", "git diff"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="cd clean; git diff; cd evil; git diff")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    git_permissions = [
        required
        for required in permission.required_permissions
        if required.label == "git diff"
    ]
    assert len(git_permissions) == 1
    assert str(evil.resolve()) in git_permissions[0].invocation_pattern


@pytest.mark.parametrize(
    "command",
    [
        "pushd evil; git diff",
        "Push-Location evil; git diff",
        "Push-Location evil; Pop-Location; git diff",
    ],
)
def test_windows_shell_tracks_directory_stack_builtins(command, tmp_path, monkeypatch):
    evil = tmp_path / "evil"
    for repository in (tmp_path, evil):
        (repository / ".git").mkdir(parents=True)
        (repository / ".git" / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
        )
    (evil / ".git" / "config").write_text("[diff]\n\texternal = evil-diff.exe\n")
    monkeypatch.chdir(tmp_path)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            allowlist=["pushd", "Push-Location", "Pop-Location", "git diff"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize(
    ("command", "config"),
    [
        ("git status", "[core]\n\tfsmonitor = monitor.exe\n"),
        ("git status", '[filter "unsafe"]\n\tclean = clean.exe\n'),
        ("git diff", '[diff "unsafe"]\n\tcommand = diff.exe\n'),
        ("git log", "[gpg]\n\tprogram = fake-gpg.exe\n"),
        ("git log", '[merge "unsafe"]\n\tdriver = merge.exe\n'),
    ],
    ids=[
        "fsmonitor",
        "clean-filter",
        "external-diff",
        "signature-program",
        "merge-driver",
    ],
)
def test_windows_shell_git_readers_require_approval_for_executable_repository_config(
    command, config, tmp_path, monkeypatch
):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(config)
    monkeypatch.chdir(tmp_path)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[command]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_windows_shell_git_reader_approval_is_scoped_to_the_repository(
    tmp_path, monkeypatch
):
    patterns = []
    for name in ("first", "second"):
        repository = tmp_path / name
        (repository / ".git").mkdir(parents=True)
        (repository / ".git" / "config").write_text(
            "[core]\n\tfsmonitor = monitor.exe\n"
        )
        monkeypatch.chdir(repository)
        tool = WindowsShell(
            config_getter=lambda: WindowsShellToolConfig(allowlist=["git status"]),
            state=BaseToolState(),
        )
        permission = tool.resolve_permission(WindowsShellArgs(command="git status"))
        assert isinstance(permission, PermissionContext)
        assert permission.permission is ToolPermission.ASK
        assert len(permission.required_permissions) == 1
        patterns.append(permission.required_permissions[0].session_pattern)

    assert patterns[0] != patterns[1]


@pytest.mark.parametrize("command", ["git.exe status", r"C:\tools\git.exe status"])
def test_windows_executable_suffixes_do_not_skip_git_guardrails(
    command, tmp_path, monkeypatch
):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n\tfsmonitor = monitor.exe\n")
    monkeypatch.chdir(tmp_path)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[command]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_windows_executable_suffixes_do_not_skip_less_guardrails():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["less"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="less.exe --lesskey-src=keys input.txt")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_windows_shell_path_qualified_denylist_matches_basename():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(denylist=["cmd /k"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"C:\Windows\System32\cmd.exe /k echo hi")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "cmd /k" in (permission.reason or "")


def test_windows_shell_path_qualified_standalone_denylist_matches_stem():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(denylist_standalone=["notepad"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"C:\Windows\System32\notepad.exe")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER


def test_experimental_windows_shell_permission_requires_approval_for_outside_cwd(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    tool = ExperimentalWindowsShell(
        config_getter=lambda: ExperimentalBashToolConfig(allowlist=["dir"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        ExperimentalBashArgs(command="dir", cwd=str(outside))
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


@pytest.mark.parametrize(
    "command_template",
    [
        "dir {directory}",
        "gci {directory}",
        "ls {directory}",
        "type {file}",
        "cat {file}",
        "gc {file}",
        "findstr needle {file}",
        "DIR {directory}",
    ],
)
def test_windows_shell_read_command_requires_approval_for_outside_path(
    command_template, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("needle", encoding="utf-8")
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(), state=BaseToolState()
    )
    command = command_template.format(directory=outside, file=outside_file)

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_config_uses_native_defaults_with_git_bash(monkeypatch):
    monkeypatch.setattr(experimental_bash_module, "is_windows", lambda: True)
    monkeypatch.setattr(bash_module, "uses_posix_shell", lambda: True)

    config = WindowsShellToolConfig()

    assert "dir" in config.allowlist
    assert "ls" not in config.allowlist
    assert "cmd /k" in config.denylist
    assert "cmd" in config.denylist_standalone


def test_git_bash_config_uses_posix_defaults_on_native_windows(monkeypatch):
    monkeypatch.setattr(experimental_bash_module, "is_windows", lambda: True)
    monkeypatch.setattr(bash_module, "uses_posix_shell", lambda: False)

    config = GitBashToolConfig()

    assert "ls" in config.allowlist
    assert "dir" not in config.allowlist
    assert "bash -i" in config.denylist
    assert "cmd /k" not in config.denylist


def test_git_bash_permission_uses_posix_guardrails_on_native_windows():
    tool = GitBash(
        config_getter=lambda: GitBashToolConfig(denylist=["rm"]), state=BaseToolState()
    )

    permission = tool.resolve_permission(GitBashArgs(command="rm -rf build"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "rm" in (permission.reason or "")


@pytest.mark.parametrize(
    "command_template",
    [
        "cat x#y {outside}",
        "ls x#y {outside}",
        "find . -name x#y -o -delete",
        r"cat x\ #y {outside}",
        r"cat \"x {outside} \"",
        r"cat \'x {outside} \'",
    ],
)
def test_git_bash_guardrail_sees_the_tokens_git_bash_runs(
    command_template, tmp_path, monkeypatch
):
    monkeypatch.setattr(experimental_bash_module, "is_windows", lambda: True)
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside" / "secret.txt"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    tool = ExperimentalGitBash(
        config_getter=lambda: GitBashToolConfig(), state=BaseToolState()
    )

    permission = tool.resolve_permission(
        ExperimentalBashArgs(
            command=command_template.format(outside=outside.as_posix())
        )
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_experimental_git_bash_does_not_reject_native_windows(monkeypatch):
    monkeypatch.setattr(experimental_bash_module, "is_windows", lambda: True)
    tool = ExperimentalGitBash(
        config_getter=lambda: GitBashToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(ExperimentalBashArgs(command="echo hi"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


windows_only = pytest.mark.skipif(
    not is_windows(), reason="Windows shell integration requires native Windows"
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (r"echo x > C:\Users\me\profile.ps1", ((r"C:\Users\me\profile.ps1",) * 2,)),
        (r"echo x > \\server\share\x.txt", ((r"\\server\share\x.txt",) * 2,)),
        (r"echo x > C:\PROGRA~1\x.txt", ((r"C:\PROGRA~1\x.txt",) * 2,)),
        (r"echo x > 'C:\a b\x.txt'", ((r"'C:\a b\x.txt'", r"C:\a b\x.txt"),)),
        (r'echo x > "C:\a b\x.txt"', ((r'"C:\a b\x.txt"', r"C:\a b\x.txt"),)),
        # Listed as read; whether it names one file is the caller's to judge.
        ("echo x > /Users/me/x.txt", (("/Users/me/x.txt",) * 2,)),
        (r"echo x > \Users\me\x.txt", ((r"\Users\me\x.txt",) * 2,)),
        ("echo x > C:x.txt", (("C:x.txt",) * 2,)),
        # A script block or subexpression may run, so its redirects count.
        (r"& { 'x' > C:\Users\me\x.ps1 }", ((r"C:\Users\me\x.ps1",) * 2,)),
        (
            r"echo $('x' > C:\a\inner) > C:\a\outer",
            ((r"C:\a\outer",) * 2, (r"C:\a\inner",) * 2),
        ),
        # From a root, but only PowerShell can read the spelling.
        (r'echo x > "C:\a\$name.txt"', ((r'"C:\a\$name.txt"', None),)),
        (r"echo x > C:\work\*.txt", ((r"C:\work\*.txt", None),)),
        ("echo x > C:\\work\\`\n..\\x", (("C:\\work\\`\n..\\x", None),)),
        ("echo x > `\nC:\\x", (("`\nC:\\x", None),)),
        (r"echo x > \\?\C:\x", ((r"\\?\C:\x", None),)),
        # An array target: the reader cannot place a list, whatever comes first.
        (
            r"echo x > C:\ws\a.txt, C:\Users\me\x.plist",
            ((r"C:\ws\a.txt, C:\Users\me\x.plist", None),),
        ),
        (
            r"echo x > $null, C:\Users\me\x.plist",
            ((r"$null, C:\Users\me\x.plist", None),),
        ),
        (
            r"echo x > (C:\ws\a.txt, C:\Users\me\x.plist)",
            ((r"(C:\ws\a.txt, C:\Users\me\x.plist)", None),),
        ),
        ("echo x > @(1)", (("@(1)", None),)),
        # Relative, variable-led or discarded: not listed.
        ("echo x > out.txt", ()),
        (r"echo x > $env:TEMP\x.txt", ()),
        (r"echo x > %TEMP%\x.txt", ()),
        ("echo x > $null", ()),
        ("echo x > NUL 2>&1", ()),
    ],
)
def test_windows_redirect_writes_place_only_plainly_spelled_targets(command, expected):
    """A target from a root -- a drive, a share or the current drive's root --
    is read exactly where it is spelled plainly, and listed unread otherwise.
    One led by a variable or relative to the current location is not listed;
    any other spelling is listed unread.
    """
    writes = windows_shell_module.windows_redirect_writes(command)

    assert tuple((write.spelling, write.path) for write in writes) == expected


@pytest.mark.parametrize(
    "command", ["copy secret.txt /dev/null", "git log 2>/dev/null"]
)
def test_windows_shell_reads_dev_null_as_a_real_path(command: str):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(permission=ToolPermission.ALWAYS),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert PermissionScope.OUTSIDE_DIRECTORY in [
        required.scope for required in permission.required_permissions
    ]
