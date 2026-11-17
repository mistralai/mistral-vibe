"""Directory-changing readers keep command and file approvals separate."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._unified_permissions import UnifiedPermissionResolver
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.permissions import PermissionStore
from vibe.core.utils import is_windows
from vibe.permissions import PermissionScope, RequiredPermission

pytestmark = pytest.mark.skipif(is_windows(), reason="POSIX active shell selection")

CWD_READS = [
    "cd sub && cat .env",
    "cd sub && cat .e*",
    "cd sub && cat notes",
    "cd sub; cat .env",
    "cd missing || cat .env",
    "cd sub && cd .. && cat .env",
    "pushd sub && cat .env",
    "pushd sub; popd; cat .env",
    'cd "$TARGET" && cat .env',
    "cd s* && cat .env",
    "cd -- sub && cat .env",
    "cd -P sub && cat .env",
    "eval 'cd sub && cat .env'",
    "eval 'cd sub'; cat .env",
    "(cd sub && cat .env)",
    "builtin cd sub && cat .env",
    "command cd sub && cat .env",
]


def resolver_for(cwd: Path, managed: bool, *, disabled: bool = False):
    config = build_test_vibe_config(
        managed_shell_tools_enabled=managed,
        tools={"bash": {"sensitive_file_patterns": []}} if disabled else {},
    )
    orchestrator = FakeConfigOrchestrator(config)
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=cwd,
        harness_files=HarnessFilesManager().for_session(cwd),
        permission_getter=store.get_tool_permission,
    )
    return UnifiedPermissionResolver(manager, store, orchestrator), store


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    for directory in [tmp_path, tmp_path / "sub"]:
        (directory / ".env").write_text("SYNTHETIC_CWD_SECRET\n")
        (directory / ".env.local").write_text("SYNTHETIC_NEIGHBOR_SECRET\n")
        (directory / "ordinary.txt").write_text("ORDINARY\n")
    (tmp_path / "notes").write_text("ORDINARY\n")
    (tmp_path / "sub" / "notes").symlink_to(tmp_path / "sub" / ".env")
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("route", ["file_system.bash", "process.start"])
@pytest.mark.parametrize("command", CWD_READS)
async def test_cwd_sensitive_reads_have_exact_command_and_no_inferred_file_grants(
    managed: bool, route: str, command: str, files: Path
) -> None:
    resolver, store = resolver_for(files, managed)
    # The whole shell permission must not erase independent read authority.
    store.set_tool_permission("bash", ToolPermission.ALWAYS)
    initial = await resolver.resolve(route, {"command": command})
    assert initial.decision == "ask", initial
    required = tuple(
        RequiredPermission.model_validate(rp) for rp in initial.required_permissions
    )
    assert not any(rp.scope is PermissionScope.FILE_PATTERN for rp in required), (
        required
    )
    exact = [
        rp
        for rp in required
        if rp.scope is PermissionScope.COMMAND_PATTERN
        and rp.invocation_pattern == command
        and rp.session_pattern == command
    ]
    assert exact, required
    await resolver.grant(route, required, permanent=False)
    assert (await resolver.resolve(route, {"command": command})).decision == "allow"
    changed = command.replace(".env", ".env.local")
    if changed == command:
        changed = "cd sub && cat .env.local"
    assert (await resolver.resolve(route, {"command": changed})).decision == "ask"
    # The exact script approval is not a grant for either possible-cwd file.
    assert (await resolver.resolve(route, {"command": "cat .env"})).decision == "ask"
    assert (
        await resolver.resolve(route, {"command": "cat sub/.env"})
    ).decision == "ask"


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("route", ["file_system.bash", "process.start"])
@pytest.mark.parametrize(
    "command",
    [
        "cd sub && cat ordinary.txt",
        "cd sub; cat ordinary.txt",
        "cd sub && head -1 ordinary.txt",
        "cd sub && grep token ordinary.txt",
    ],
)
async def test_known_cwd_change_keeps_ordinary_reads_automatic(
    managed: bool, route: str, command: str, files: Path
) -> None:
    """A literal cd is followed, so a read that is ordinary from every reachable
    directory needs no approval.
    """
    resolver, _ = resolver_for(files, managed)
    assert (await resolver.resolve(route, {"command": command})).decision == "allow"


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("route", ["file_system.bash", "process.start"])
async def test_no_cwd_change_preserves_ordinary_and_sensitive_file_controls(
    managed: bool, route: str, files: Path
) -> None:
    resolver, _ = resolver_for(files, managed)
    assert (
        await resolver.resolve(route, {"command": "cat ordinary.txt"})
    ).decision == "allow"
    sensitive = await resolver.resolve(route, {"command": "cat .env"})
    assert sensitive.decision == "ask"
    required = tuple(
        RequiredPermission.model_validate(rp) for rp in sensitive.required_permissions
    )
    assert [
        rp.invocation_pattern
        for rp in required
        if rp.scope is PermissionScope.FILE_PATTERN
    ] == [str(files / ".env")]


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("route", ["file_system.bash", "process.start"])
async def test_later_cwd_change_does_not_create_unread_grants(
    managed: bool, route: str, files: Path
) -> None:
    resolver, _ = resolver_for(files, managed)
    assert (
        await resolver.resolve(route, {"command": "cat ordinary.txt; cd sub"})
    ).decision == "allow"
    sensitive = await resolver.resolve(route, {"command": "cat .env; cd sub"})
    assert sensitive.decision == "ask"
    required = tuple(
        RequiredPermission.model_validate(rp) for rp in sensitive.required_permissions
    )
    assert [
        rp.invocation_pattern
        for rp in required
        if rp.scope is PermissionScope.FILE_PATTERN
    ] == [str(files / ".env")]


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("route", ["file_system.bash", "process.start"])
async def test_disabled_patterns_preserve_explicit_configuration(
    managed: bool, route: str, files: Path
) -> None:
    resolver, store = resolver_for(files, managed, disabled=True)
    store.set_tool_permission("bash", ToolPermission.ALWAYS)
    for command in ["cat .env", "cd sub && cat .env", "cd sub && cat notes"]:
        outcome = await resolver.resolve(route, {"command": command})
        assert outcome.decision == "allow", outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("route", ["file_system.bash", "process.start"])
@pytest.mark.parametrize(
    "prefix",
    [
        'eval "$CWD_SCRIPT"',
        'builtin eval "$CWD_SCRIPT"',
        '"$CWD_COMMAND" child',
        ". ./change-dir.sh",
        "source ./change-dir.sh",
        'command "$CWD_COMMAND" child',
    ],
)
async def test_opaque_parent_cwd_never_grants_unread_session_file(
    managed: bool,
    route: str,
    prefix: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    import subprocess

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SYNTHETIC_SESSION_SECRET\n")
    (tmp_path / "child").mkdir()
    (tmp_path / "child" / ".env").write_text("SYNTHETIC_CHILD_SECRET\n")
    (tmp_path / "change-dir.sh").write_text("cd child\n")
    monkeypatch.setenv("CWD_SCRIPT", "cd child")
    monkeypatch.setenv("CWD_COMMAND", "cd")
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("Bash is required for builtin/source compatibility")
    command = f"{prefix}; cat .env"
    # Only this fully constructed inert fixture script is executed.
    oracle = subprocess.run(
        [shell, "-c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=3,
    )
    assert oracle.stdout == "SYNTHETIC_CHILD_SECRET\n"
    resolver, store = resolver_for(tmp_path, managed)
    store.set_tool_permission("bash", ToolPermission.ALWAYS)
    result = await resolver.resolve(route, {"command": command})
    assert result.decision == "ask"
    required = tuple(
        RequiredPermission.model_validate(rp) for rp in result.required_permissions
    )
    assert not any(rp.scope is PermissionScope.FILE_PATTERN for rp in required)
    assert any(
        rp.scope is PermissionScope.COMMAND_PATTERN
        and rp.invocation_pattern == command
        and rp.session_pattern == command
        for rp in required
    )
    await resolver.grant(route, required, permanent=False)
    assert (await resolver.resolve(route, {"command": command})).decision == "allow"
    assert (await resolver.resolve(route, {"command": "cat .env"})).decision == "ask"
    assert (
        await resolver.resolve(route, {"command": "cat child/.env"})
    ).decision == "ask"


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("route", ["file_system.bash", "process.start"])
@pytest.mark.parametrize(
    "command",
    [
        'cat .env; eval "$CWD_SCRIPT"',
        "sh -c 'cd child'; cat .env",
        "bash -c 'cd child'; cat .env",
        "env sh -c 'cd child'; cat .env",
        "exec; cat .env",
    ],
)
async def test_later_opaque_or_child_cwd_keeps_actual_parent_file_grant(
    managed: bool, route: str, command: str, files: Path
) -> None:
    resolver, _ = resolver_for(files, managed)
    result = await resolver.resolve(route, {"command": command})
    assert result.decision == "ask"
    required = tuple(
        RequiredPermission.model_validate(rp) for rp in result.required_permissions
    )
    assert [
        rp.invocation_pattern
        for rp in required
        if rp.scope is PermissionScope.FILE_PATTERN
    ] == [str(files / ".env")]


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("route", ["file_system.bash", "process.start"])
@pytest.mark.parametrize("always", [False, True])
@pytest.mark.parametrize(
    "command",
    [
        "sh -c 'cd sub && cat .env'",
        "bash -c 'cd sub && cat .env'",
        "command sh -c 'cd sub && cat .env'",
        "exec sh -c 'cd sub && cat .env'",
        "env sh -c 'cd sub && cat .env'",
        "command -p sh -c 'cd sub && cat .env'",
        "exec sh -c 'cd sub && cat .env'; cat .env",
    ],
)
async def test_child_reader_interpreter_grants_only_original_command(
    managed: bool, route: str, always: bool, command: str, files: Path
) -> None:
    resolver, store = resolver_for(files, managed)
    if always:
        store.set_tool_permission("bash", ToolPermission.ALWAYS)
    result = await resolver.resolve(route, {"command": command})
    assert result.decision == "ask"
    required = tuple(
        RequiredPermission.model_validate(rp) for rp in result.required_permissions
    )
    assert not any(rp.scope is PermissionScope.FILE_PATTERN for rp in required)
    assert any(
        rp.scope is PermissionScope.COMMAND_PATTERN
        and rp.invocation_pattern == command
        and rp.session_pattern == command
        for rp in required
    )
    await resolver.grant(route, required, permanent=False)
    assert (await resolver.resolve(route, {"command": command})).decision == "allow"
    changed = command.replace(".env", ".env.local")
    assert (await resolver.resolve(route, {"command": changed})).decision == "ask"
    assert (await resolver.resolve(route, {"command": "cat .env"})).decision == "ask"
    assert (
        await resolver.resolve(route, {"command": "cat sub/.env"})
    ).decision == "ask"
