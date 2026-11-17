from __future__ import annotations

from pathlib import Path
import shlex

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._unified_permissions import UnifiedPermissionResolver
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.permissions import PermissionStore
from vibe.core.utils import is_windows
from vibe.permissions import PermissionScope, RequiredPermission

pytestmark = pytest.mark.skipif(is_windows(), reason="Active POSIX shell selection")


def _resolver(
    cwd: Path, managed: bool, scratchpad_dir: Path | None = None
) -> tuple[UnifiedPermissionResolver, PermissionStore]:
    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(managed_shell_tools_enabled=managed)
    )
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=cwd,
        scratchpad_dir=scratchpad_dir,
        harness_files=HarnessFilesManager().for_session(cwd),
        permission_getter=store.get_tool_permission,
    )
    assert type(manager.get("bash")).__name__ == (
        "ExperimentalBash" if managed else "Bash"
    )
    return UnifiedPermissionResolver(manager, store, orchestrator), store


def _wrapped(wrapper: str, executable: str, path: Path) -> str:
    inner = shlex.join([executable, str(path)])
    return (
        "eval " + shlex.quote(inner)
        if wrapper == "quoted eval"
        else f"{wrapper} {inner}"
    )


async def _grant_command(resolver: UnifiedPermissionResolver, command: str) -> None:
    outcome = await resolver.resolve("file_system.bash", {"command": command})
    assert outcome.decision == "ask"
    permissions = tuple(
        RequiredPermission.model_validate(permission)
        for permission in outcome.required_permissions
    )
    assert all(
        permission.scope
        not in {PermissionScope.OUTSIDE_DIRECTORY, PermissionScope.FILE_PATTERN}
        for permission in permissions
    )
    await resolver.grant("file_system.bash", permissions, permanent=False)


async def _uncovered_path_permission(
    resolver: UnifiedPermissionResolver,
    store: PermissionStore,
    command: str,
    scope: PermissionScope,
    target: Path,
) -> RequiredPermission:
    outcome = await resolver.resolve("file_system.bash", {"command": command})
    assert outcome.decision == "ask"
    permissions = [
        RequiredPermission.model_validate(permission)
        for permission in outcome.required_permissions
        if permission["scope"] == scope.value
    ]
    assert len(permissions) == 1
    permission = permissions[0]
    assert permission.invocation_pattern == str(target.resolve())
    names = resolver.vibe_tool_names("file_system.bash")
    assert names
    assert all(not store.covers(name, permission) for name in names)
    return permission


async def _grant_path(
    resolver: UnifiedPermissionResolver,
    store: PermissionStore,
    command: str,
    scope: PermissionScope,
    target: Path,
) -> None:
    permission = await _uncovered_path_permission(
        resolver, store, command, scope, target
    )
    await resolver.grant("file_system.bash", (permission,), permanent=False)
    assert all(
        store.covers(name, permission)
        for name in resolver.vibe_tool_names("file_system.bash")
    )
    again = await resolver.resolve("file_system.bash", {"command": command})
    assert all(
        required["scope"] != scope.value for required in again.required_permissions
    )


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.asyncio
async def test_dynamic_sensitive_read_approval_cannot_grant_its_literal_spelling(
    tmp_path: Path, managed: bool
) -> None:
    (tmp_path / ".env.$SUFFIX").write_text("synthetic literal fixture\n")
    resolver, _ = _resolver(tmp_path, managed)
    command = 'cat ".env.$SUFFIX"'
    first = await resolver.resolve("file_system.bash", {"command": command})
    assert first.decision == "ask"
    required = tuple(
        RequiredPermission.model_validate(rp) for rp in first.required_permissions
    )
    assert len(required) == 1
    assert required[0].scope is PermissionScope.COMMAND_PATTERN
    assert required[0].session_pattern == command
    await resolver.grant("file_system.bash", required, permanent=False)
    same = await resolver.resolve("file_system.bash", {"command": command})
    assert same.decision == "allow"
    other = await resolver.resolve(
        "file_system.bash", {"command": "cat '.env.$SUFFIX'"}
    )
    assert other.decision == "ask"
    assert any(
        rp["scope"] == PermissionScope.FILE_PATTERN.value
        for rp in other.required_permissions
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("wrapper", ["eval", "quoted eval", "exec", "exec --"])
@pytest.mark.parametrize("executable", ["cat", "/bin/cat"])
@pytest.mark.parametrize("sensitive", [False, True])
async def test_wrapper_command_grant_retains_independent_path_approval(
    tmp_path: Path, managed: bool, wrapper: str, executable: str, sensitive: bool
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ordinary = workspace / "ordinary.txt"
    ordinary.write_text("ordinary\n")
    target = workspace / ".env" if sensitive else tmp_path / "outside" / "ordinary.txt"
    target.parent.mkdir(exist_ok=True)
    target.write_text("synthetic fixture\n")
    resolver, store = _resolver(workspace, managed)
    await _grant_command(resolver, _wrapped(wrapper, executable, ordinary))
    command = _wrapped(wrapper, executable, target)

    expected_scope = (
        PermissionScope.FILE_PATTERN if sensitive else PermissionScope.OUTSIDE_DIRECTORY
    )
    await _grant_path(resolver, store, command, expected_scope, target)
    other = target.with_name(".env.other" if sensitive else "other.txt")
    other.write_text("different synthetic fixture\n")
    await _uncovered_path_permission(
        resolver, store, _wrapped(wrapper, executable, other), expected_scope, other
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("executable", ["cat", "/bin/cat"])
async def test_exec_outside_path_with_spaces_keeps_its_exact_permission_target(
    tmp_path: Path, managed: bool, executable: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ordinary = workspace / "ordinary.txt"
    ordinary.write_text("ordinary\n")
    target = tmp_path / "outside" / "ordinary file.txt"
    target.parent.mkdir()
    target.write_text("synthetic fixture\n")
    resolver, store = _resolver(workspace, managed)
    await _grant_command(resolver, _wrapped("exec", executable, ordinary))
    await _grant_path(
        resolver,
        store,
        _wrapped("exec", executable, target),
        PermissionScope.OUTSIDE_DIRECTORY,
        target,
    )
    other = target.with_name("other file.txt")
    other.write_text("different synthetic fixture\n")
    await _uncovered_path_permission(
        resolver,
        store,
        _wrapped("exec", executable, other),
        PermissionScope.OUTSIDE_DIRECTORY,
        other,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
async def test_qualified_reader_is_not_added_to_the_command_allowlist(
    tmp_path: Path, managed: bool
) -> None:
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("ordinary\n")
    resolver, _ = _resolver(tmp_path, managed)
    assert (
        await resolver.resolve("file_system.bash", {"command": f"cat {ordinary}"})
    ).decision == "allow"
    qualified = await resolver.resolve(
        "file_system.bash", {"command": f"/bin/cat {ordinary}"}
    )
    assert qualified.decision == "ask"
    assert all(
        permission["scope"] != PermissionScope.OUTSIDE_DIRECTORY.value
        for permission in qualified.required_permissions
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("wrapper", ["eval", "quoted eval", "exec", "exec --"])
async def test_wrapper_keeps_the_owned_scratchpad_exemption(
    tmp_path: Path, managed: bool, wrapper: str
) -> None:
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("ordinary\n")
    scratchpad = tmp_path.parent / f"{tmp_path.name}-scratchpad"
    resolver, _ = _resolver(tmp_path, managed, scratchpad_dir=scratchpad)
    await _grant_command(resolver, _wrapped(wrapper, "cat", ordinary))
    scratchpad.mkdir(parents=True, exist_ok=True)
    target = scratchpad / ".env"
    target.write_text("synthetic scratchpad\n")

    result = await resolver.resolve(
        "file_system.bash", {"command": _wrapped(wrapper, "cat", target)}
    )

    assert all(
        permission["scope"]
        not in {
            PermissionScope.OUTSIDE_DIRECTORY.value,
            PermissionScope.FILE_PATTERN.value,
        }
        for permission in result.required_permissions
    )
