from __future__ import annotations

import importlib
from pathlib import Path
import subprocess

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
from vibe.core.tools.models import ApprovedRule
from vibe.core.tools.permissions import PermissionContext, PermissionStore
from vibe.core.utils import is_windows
from vibe.core.workspace import Workspace
from vibe.permissions import PathGrantScope, PermissionScope, path_grant_pattern
from vibe.utils import paths


def _sensitive_windows_tool(
    kind: str, cwd: Path, permission: ToolPermission = ToolPermission.ALWAYS
):
    config = WindowsShellToolConfig(
        permission=permission,
        allowlist=[
            "Get-Content",
            "Select-String",
            "gc",
            "cat",
            "type",
            "more",
            "findstr",
            "pwsh",
            "powershell",
            "cmd",
            "Invoke-Expression",
            "iex",
        ],
    )
    tool_type = WindowsShell if kind == "fallback" else ExperimentalWindowsShell
    return tool_type(config_getter=lambda: config, state=BaseToolState(), cwd=cwd)


def _resolve_sensitive_windows(
    tool: WindowsShell | ExperimentalWindowsShell, command: str
) -> PermissionContext | None:
    if isinstance(tool, WindowsShell):
        return tool.resolve_permission(WindowsShellArgs(command=command))
    return tool.resolve_permission(ExperimentalBashArgs(command=command))


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize("permission", [ToolPermission.ASK, ToolPermission.ALWAYS])
@pytest.mark.parametrize(
    "command",
    [
        "Get-Content .env",
        "gc -LiteralPath .env",
        "cat .env",
        "type .env",
        "more .env",
        "findstr x .env",
        "Select-String -Pattern x -Path .env",
        "sls x .env",
    ],
)
def test_windows_sensitive_reader_overrides_command_approval(
    kind, permission, command, tmp_path
):
    tool = _sensitive_windows_tool(kind, tmp_path, permission)
    result = _resolve_sensitive_windows(tool, command)
    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    assert any(
        rp.scope is PermissionScope.FILE_PATTERN for rp in result.required_permissions
    )


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize(
    "parameter,expected", [("-Path", ".env.a1"), ("-LiteralPath", ".env.a[1]")]
)
def test_windows_sensitive_path_grants_match_the_reader_operand(
    kind, parameter, expected, tmp_path
):
    for name in [".env.a[1]", ".env.a1"]:
        (tmp_path / name).touch()
    tool = _sensitive_windows_tool(kind, tmp_path)
    result = _resolve_sensitive_windows(tool, f"Get-Content {parameter} '.env.a[1]'")
    assert isinstance(result, PermissionContext)
    files = [
        rp
        for rp in result.required_permissions
        if rp.scope is PermissionScope.FILE_PATTERN
    ]
    assert [rp.invocation_pattern for rp in files] == [str(tmp_path / expected)]
    store = PermissionStore()
    for rp in result.required_permissions:
        store.add_rule(
            ApprovedRule(
                tool_name="powershell",
                scope=rp.scope,
                session_pattern=rp.session_pattern,
            )
        )
    other = ".env.a[1]" if expected == ".env.a1" else ".env.a1"
    other_read = _resolve_sensitive_windows(tool, f"Get-Content -LiteralPath '{other}'")
    assert isinstance(other_read, PermissionContext)
    assert any(
        not store.covers("powershell", rp) for rp in other_read.required_permissions
    )


@pytest.mark.parametrize("kind", ["fallback", "managed"])
def test_windows_dynamic_reader_grants_no_unread_literal_file(kind, tmp_path):
    tool = _sensitive_windows_tool(kind, tmp_path)
    command = 'Get-Content ".env.$SUFFIX"'
    result = _resolve_sensitive_windows(tool, command)
    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    assert [rp.session_pattern for rp in result.required_permissions] == [command]
    literal = _resolve_sensitive_windows(
        tool, "Get-Content -LiteralPath '.env.$SUFFIX'"
    )
    assert isinstance(literal, PermissionContext)
    assert any(
        rp.scope is PermissionScope.FILE_PATTERN for rp in literal.required_permissions
    )


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize("permission", [ToolPermission.ASK, ToolPermission.ALWAYS])
@pytest.mark.parametrize(
    "command",
    [
        "& $reader .env",
        'Invoke-Expression "Get-Content .env"',
        'iex "Get-Content .env"',
        'pwsh -Command "Get-Content .env"',
        'powershell -Command "Get-Content .env"',
        "cmd /C type .env",
        "& { Get-Content .env }",
    ],
)
def test_windows_interpreter_grant_keeps_only_the_original_invocation(
    kind, permission, command, tmp_path
):
    tool = _sensitive_windows_tool(kind, tmp_path, permission)
    result = _resolve_sensitive_windows(tool, command)
    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    command_permissions = [
        rp
        for rp in result.required_permissions
        if rp.scope is PermissionScope.COMMAND_PATTERN
    ]
    assert [rp.session_pattern for rp in command_permissions] == [command]
    store = PermissionStore()
    for rp in result.required_permissions:
        store.add_rule(
            ApprovedRule(
                tool_name="powershell",
                scope=rp.scope,
                session_pattern=rp.session_pattern,
            )
        )
    same = _resolve_sensitive_windows(tool, command)
    assert isinstance(same, PermissionContext)
    assert all(store.covers("powershell", rp) for rp in same.required_permissions)
    other = _resolve_sensitive_windows(tool, command.replace(".env", ".env.a1"))
    assert isinstance(other, PermissionContext)
    assert any(not store.covers("powershell", rp) for rp in other.required_permissions)


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "Get-Content .env",
        "Get-Content -Unknown .env",
        'Invoke-Expression "Get-Content .env"',
    ],
)
def test_windows_disabled_sensitive_patterns_preserve_auto_approval(
    kind, command, tmp_path
):
    config = WindowsShellToolConfig(
        permission=ToolPermission.ALWAYS, sensitive_file_patterns=[]
    )
    tool_type = WindowsShell if kind == "fallback" else ExperimentalWindowsShell
    tool = tool_type(config_getter=lambda: config, state=BaseToolState(), cwd=tmp_path)
    result = _resolve_sensitive_windows(tool, command)
    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ALWAYS


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize("patterns", [[], ["**/.env"]])
@pytest.mark.parametrize("route", ["file_system.bash", "process.start"])
@pytest.mark.parametrize(
    "invocation",
    [
        "ambiguous-reader",
        "opaque-redirect",
        "outside-interpreter",
        "dynamic-executable",
    ],
)
async def test_windows_sensitive_analysis_preserves_outside_path_denial(
    kind, patterns, route, invocation, tmp_path, monkeypatch
):
    from tests.conftest import build_test_vibe_config
    from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
    from vibe.app_server._unified_permissions import UnifiedPermissionResolver
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.tools.manager import ToolManager

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    file = outside / "ordinary.txt"
    file.write_text("synthetic outside fixture\n")
    executable = outside / "pwsh.exe"
    commands = {
        "ambiguous-reader": f"Get-Content -Unknown '{file}'",
        "opaque-redirect": f"iex 'Write-Output fixture' > '{file}'",
        "outside-interpreter": f"& '{executable}' -Command 'Write-Output fixture'",
        "dynamic-executable": f"& $reader '{file}'",
    }
    tool_type = WindowsShell if kind == "fallback" else ExperimentalWindowsShell
    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(
            managed_shell_tools_enabled=kind == "managed",
            tools={
                "powershell": {
                    "permission": "always",
                    "outside_workdir": "deny",
                    "sensitive_file_patterns": patterns,
                }
            },
        )
    )
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=workspace,
        harness_files=HarnessFilesManager().for_session(workspace),
        permission_getter=store.get_tool_permission,
    )
    manager._all_tools = {"powershell": tool_type}
    manager._tool_variants_by_name = {"powershell": [tool_type]}
    monkeypatch.setattr(manager, "_is_tool_available", lambda cls: cls is tool_type)
    tool = manager.get("powershell")
    assert isinstance(tool, (WindowsShell, ExperimentalWindowsShell))
    required = _resolve_sensitive_windows(tool, commands[invocation])
    assert isinstance(required, PermissionContext)
    assert not any(
        rp.scope is PermissionScope.FILE_PATTERN for rp in required.required_permissions
    )
    resolver = UnifiedPermissionResolver(manager, store, orchestrator)
    outcome = await resolver.resolve(route, {"command": commands[invocation]})
    assert outcome.decision == "deny"


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize(
    "command,expected",
    [
        ("Get-Content -Path .e*", {".env", ".env.a1", ".env.a[1]"}),
        ("Get-Content -LiteralPath:.env.a[1]", {".env.a[1]"}),
        ("Select-String .env.a[1] '.env.a[1]'", {".env.a1"}),
        ("Select-String -Pattern '.env','x' -LiteralPath .env.a[1]", {".env.a[1]"}),
        ("Get-Content -Path '.env.a`[1`]'", {".env.a[1]"}),
        ("Get-Content -Tail 1 -LiteralPath .env", {".env"}),
        ("Select-String -Pattern '.env' -InputObject '.env.a[1]'", set()),
        ("Select-String '-Pattern' .env", {".env"}),
        ("Select-String '-Path' .env", {".env"}),
        ("Get-Content -Filter '*.txt' -Path .env*", set()),
        ("Get-Content -Stream alternate -LiteralPath .env", set()),
        ("Get-Content -Unknown .env", set()),
        ("Get-Content -LiteralPath '.env.$SUFFIX'", {".env.$SUFFIX"}),
        ('Get-Content -LiteralPath ".env.`$SUFFIX"', {".env.$SUFFIX"}),
    ],
)
def test_windows_reader_role_binding_does_not_nominate_unread_files(
    kind, command, expected, tmp_path
):
    for name in [".env", ".env.a1", ".env.a[1]", "ordinary.txt"]:
        (tmp_path / name).touch()
    tool = _sensitive_windows_tool(kind, tmp_path)
    result = _resolve_sensitive_windows(tool, command)
    assert isinstance(result, PermissionContext)
    files = {
        rp.invocation_pattern
        for rp in result.required_permissions
        if rp.scope is PermissionScope.FILE_PATTERN
    }
    assert files == {str(tmp_path / name) for name in expected}
    if any(parameter in command for parameter in ["-Filter", "-Stream", "-Unknown"]):
        assert result.permission is ToolPermission.ASK
        assert [rp.session_pattern for rp in result.required_permissions] == [command]


@pytest.mark.skipif(
    not is_windows(), reason="Requires native Windows PowerShell provider semantics"
)
@pytest.mark.parametrize(
    "parameter,expected", [("-Path", ".env.a1"), ("-LiteralPath", ".env.a[1]")]
)
@pytest.mark.parametrize("kind", ["fallback", "managed"])
def test_native_powershell_sensitive_grant_matches_the_file_actually_read(
    kind, parameter, expected, tmp_path
):
    for name in [".env.a1", ".env.a[1]"]:
        (tmp_path / name).write_text(name + "\n")
    command = f"Get-Content {parameter} '.env.a[1]'"
    spec = _windows.resolve_powershell_shell(None, None)
    executed = subprocess.run(
        [spec, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert executed.stdout.strip() == expected
    result = _resolve_sensitive_windows(
        _sensitive_windows_tool(kind, tmp_path), command
    )
    assert isinstance(result, PermissionContext)
    assert [
        rp.invocation_pattern
        for rp in result.required_permissions
        if rp.scope is PermissionScope.FILE_PATTERN
    ] == [str(tmp_path / expected)]


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


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize(
    "change",
    [
        "cd sub",
        "chdir sub",
        "sl sub",
        "Set-Location sub",
        "Set-Location -LiteralPath sub",
        "pushd sub",
        "Push-Location sub",
        "pushl sub",
        "popd",
        "Pop-Location",
        "popl",
    ],
)
@pytest.mark.parametrize("read", ["Get-Content .env", "cat .e*", "more .env"])
def test_windows_cwd_reader_approval_has_no_guessed_file_grants(
    kind, change, read, tmp_path
):
    sub = tmp_path / "sub"
    sub.mkdir()
    for directory in [tmp_path, sub]:
        (directory / ".env").touch()
        (directory / ".env.local").touch()
    (tmp_path / "notes").write_text("ordinary")
    tool = _sensitive_windows_tool(kind, tmp_path)
    command = f"{change}; {read}"
    result = _resolve_sensitive_windows(tool, command)
    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    assert not any(
        rp.scope is PermissionScope.FILE_PATTERN for rp in result.required_permissions
    )
    exact = [
        rp
        for rp in result.required_permissions
        if rp.scope is PermissionScope.COMMAND_PATTERN
        and rp.invocation_pattern == command
        and rp.session_pattern == command
        and rp.literal
    ]
    assert exact
    store = PermissionStore()
    for rp in result.required_permissions:
        store.add_rule(
            ApprovedRule(
                tool_name="powershell",
                scope=rp.scope,
                session_pattern=rp.session_pattern,
            )
        )
    repeated = _resolve_sensitive_windows(tool, command)
    assert isinstance(repeated, PermissionContext)
    assert all(store.covers("powershell", rp) for rp in repeated.required_permissions)
    changed = _resolve_sensitive_windows(tool, f"{change}; Get-Content .env.local")
    assert isinstance(changed, PermissionContext)
    assert any(
        not store.covers("powershell", rp) for rp in changed.required_permissions
    )
    direct = _resolve_sensitive_windows(tool, "Get-Content sub/.env")
    assert isinstance(direct, PermissionContext)
    assert any(
        rp.scope is PermissionScope.FILE_PATTERN and not store.covers("powershell", rp)
        for rp in direct.required_permissions
    )


@pytest.mark.parametrize("kind", ["fallback", "managed"])
def test_windows_reader_before_later_cwd_change_keeps_precise_file_grant(
    kind, tmp_path
):
    (tmp_path / ".env").touch()
    (tmp_path / "sub").mkdir()
    tool = _sensitive_windows_tool(kind, tmp_path)
    result = _resolve_sensitive_windows(tool, "Get-Content .env; Set-Location sub")
    assert isinstance(result, PermissionContext)
    files = [
        rp
        for rp in result.required_permissions
        if rp.scope is PermissionScope.FILE_PATTERN
    ]
    assert [rp.invocation_pattern for rp in files] == [str(tmp_path / ".env")]
    tool.config.sensitive_file_patterns.clear()
    result = _resolve_sensitive_windows(tool, "Set-Location sub; Get-Content .env")
    assert result is not None and result.permission is ToolPermission.ALWAYS


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize(
    "prefix",
    [
        "iex $CWD_SCRIPT",
        "Invoke-Expression $CWD_SCRIPT",
        "& $CWD_COMMAND child",
        ". ./change-dir.ps1",
        "& ./change-dir.ps1",
    ],
)
def test_windows_opaque_parent_cwd_never_grants_unread_session_file(
    kind, prefix, tmp_path
):
    (tmp_path / ".env").touch()
    (tmp_path / "change-dir.ps1").write_text("Set-Location child\n")
    (tmp_path / "child").mkdir()
    (tmp_path / "child" / ".env").touch()
    tool = _sensitive_windows_tool(kind, tmp_path)
    command = f"{prefix}; Get-Content .env"
    result = _resolve_sensitive_windows(tool, command)
    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    assert not any(
        rp.scope is PermissionScope.FILE_PATTERN for rp in result.required_permissions
    )
    assert any(
        rp.scope is PermissionScope.COMMAND_PATTERN
        and rp.invocation_pattern == command
        and rp.session_pattern == command
        and rp.literal
        for rp in result.required_permissions
    )
    store = PermissionStore()
    for rp in result.required_permissions:
        store.add_rule(
            ApprovedRule(
                tool_name="powershell",
                scope=rp.scope,
                session_pattern=rp.session_pattern,
            )
        )
    direct = _resolve_sensitive_windows(tool, "Get-Content .env")
    assert isinstance(direct, PermissionContext)
    assert any(
        rp.scope is PermissionScope.FILE_PATTERN and not store.covers("powershell", rp)
        for rp in direct.required_permissions
    )


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize(
    "suffix",
    [
        "iex $CWD_SCRIPT",
        "pwsh -Command 'Set-Location child'",
        "powershell -Command 'Set-Location child'",
    ],
)
def test_windows_later_opaque_or_child_cwd_keeps_actual_parent_file_grant(
    kind, suffix, tmp_path
):
    (tmp_path / ".env").touch()
    tool = _sensitive_windows_tool(kind, tmp_path)
    command = (
        f"Get-Content .env; {suffix}"
        if suffix.startswith("iex")
        else f"{suffix}; Get-Content .env"
    )
    result = _resolve_sensitive_windows(tool, command)
    assert isinstance(result, PermissionContext)
    files = [
        rp
        for rp in result.required_permissions
        if rp.scope is PermissionScope.FILE_PATTERN
    ]
    assert [rp.invocation_pattern for rp in files] == [str(tmp_path / ".env")]


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "dir .env | type",
        "Get-ChildItem .env | Get-Content",
        "Get-Item .env | gc",
        "'.env' | Get-Content",
        "dir . | Select-String token",
        "dir .env | sls token",
    ],
)
def test_windows_piped_reader_requires_the_exact_command(kind, command, tmp_path):
    """Get-Content and Select-String bind piped files to their path parameters."""
    (tmp_path / ".env").touch()
    tool = _sensitive_windows_tool(kind, tmp_path)
    result = _resolve_sensitive_windows(tool, command)
    assert isinstance(result, PermissionContext)
    assert result.permission is ToolPermission.ASK
    assert any(
        rp.scope is PermissionScope.COMMAND_PATTERN
        and rp.session_pattern == command
        and rp.literal
        for rp in result.required_permissions
    )


@pytest.mark.parametrize("kind", ["fallback", "managed"])
@pytest.mark.parametrize(
    "command",
    [
        "Get-Content notes.txt | Select-String token",
        "gc notes.txt | sls token",
        "dir | findstr token",
        "dir .env | sort",
    ],
)
def test_windows_piped_text_does_not_require_sensitive_approval(
    kind, command, tmp_path
):
    """Piped text is searched, not opened, so no file approval is owed."""
    (tmp_path / ".env").touch()
    (tmp_path / "notes.txt").write_text("token\n")
    tool = _sensitive_windows_tool(kind, tmp_path)
    tool.config.allowlist.extend(["dir", "sort", "sls"])
    result = _resolve_sensitive_windows(tool, command)
    assert result is not None
    assert result.permission is ToolPermission.ALWAYS


@pytest.mark.parametrize("kind", ["fallback", "managed"])
def test_windows_sensitive_file_label_names_no_other_shell(kind, tmp_path):
    tool = _sensitive_windows_tool(kind, tmp_path)
    result = _resolve_sensitive_windows(tool, "type .env")
    assert isinstance(result, PermissionContext)
    assert [
        rp.label
        for rp in result.required_permissions
        if rp.scope is PermissionScope.FILE_PATTERN
    ] == ["accessing sensitive files"]
