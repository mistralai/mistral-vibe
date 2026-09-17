"""Approving a call on the Unified path has to mean what it means on the legacy one.

The Runtime's ``tool_modes`` grant per tool. Vibe grants per call — approving
``npm test`` says nothing about ``rm -rf``. These tests hold the bridge between
the two to the behaviour ``AgentLoop.approve_always`` already has.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.models import PermissionScope, ToolPermission
from vibe.core.tools.permissions import PermissionStore
from vibe.permissions import RequiredPermission

pytest.importorskip("mistralai_vibe_local_harness.vibe")

# The resolver sits below the skip because it reaches the Harness permission port
# at module scope -- it builds `PermissionOutcome`, so importing the resolver is
# importing it. `mistralai-vibe-local-harness` is an optional extra, and an environment
# without it must skip this module rather than fail to collect it.
from vibe.app_server._unified_permissions import (
    RUST_BUILTIN_TOOL_SOURCES,
    UnifiedPermissionResolver,
)


def _resolver(
    tmp_path: Path, **tools: dict[str, Any]
) -> tuple[UnifiedPermissionResolver, FakeConfigOrchestrator[Any]]:
    """The bridge as ``build_unified_session_context`` assembles it."""
    orchestrator = FakeConfigOrchestrator(build_test_vibe_config(tools=tools))
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=tmp_path,
        harness_files=HarnessFilesManager().for_session(tmp_path),
        permission_getter=store.get_tool_permission,
    )
    return UnifiedPermissionResolver(manager, store, orchestrator), orchestrator


def _granted(outcome: Any) -> tuple[RequiredPermission, ...]:
    """The permissions as the callback round trip delivers them back: wire JSON."""
    return tuple(
        RequiredPermission.model_validate(permission, by_alias=True, by_name=False)
        for permission in outcome.required_permissions
    )


@pytest.mark.asyncio
async def test_a_shell_call_asks_scoped_to_the_command_it_would_run(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver over the default tool catalogue.
    *Do*: Resolve a bash call the user has not approved.
    *Assert*: It asks, and names the command pattern rather than the tool. The
    pattern is what a grant gets recorded against, so an unscoped ask here is
    what would turn "allow npm test" into "allow every command".
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)

    # Do
    outcome = await resolver.resolve("file_system.bash", {"command": "npm test"})

    # Assert
    assert outcome.decision == "ask"
    assert [
        permission["sessionPattern"] for permission in outcome.required_permissions
    ] == ["npm test *"]


@pytest.mark.asyncio
async def test_a_session_grant_covers_the_command_it_was_given_for(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver that has just been told a bash call is approved for the session.
    *Do*: Resolve the same command again, with an extra argument.
    *Assert*: It runs without asking. This is the bug the whole change exists for:
    the second prompt is what the user saw before.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)
    outcome = await resolver.resolve("file_system.bash", {"command": "npm test"})
    await resolver.grant("file_system.bash", _granted(outcome), permanent=False)

    # Do
    again = await resolver.resolve("file_system.bash", {"command": "npm test --watch"})

    # Assert
    assert again.decision == "allow"


@pytest.mark.asyncio
async def test_a_session_grant_does_not_cover_a_different_command(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver with a session grant for ``npm test``.
    *Do*: Resolve an unrelated destructive command.
    *Assert*: It still asks. A grant recorded against the bash *tool* would have
    silenced this one, which is exactly what per-call scoping is for.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)
    outcome = await resolver.resolve("file_system.bash", {"command": "npm test"})
    await resolver.grant("file_system.bash", _granted(outcome), permanent=False)

    # Do
    other = await resolver.resolve("file_system.bash", {"command": "rm -rf /tmp/x"})

    # Assert
    assert other.decision == "ask"
    assert other.required_permissions


def _shell_with_a_second_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``file_system.bash`` stand for two tools, as it does on Windows.

    Windows is the only platform that offers two shells at once, and the suite
    runs on POSIX, so the second source is stood in by ``read_file``: it rejects
    ``{"command": ...}``, so the resolver falls back to its configured permission
    with no pattern attached. That is the shape a shell presents when it has no
    reading of the call -- ``git_bash`` and ``powershell`` split a command with
    different parsers, so either can end up there while the other has an answer.
    Which of the two abstains is set per test by the permission ``read_file`` is
    configured with.
    """
    monkeypatch.setitem(
        RUST_BUILTIN_TOOL_SOURCES, "file_system.bash", frozenset({"bash", "read_file"})
    )


@pytest.mark.asyncio
async def test_a_sibling_with_nothing_to_scope_does_not_re_ask_a_granted_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A shell builtin standing for two tools, only one of which scopes
    the command, with that command already approved for the session.
    *Do*: Resolve the same command again.
    *Assert*: It runs. The silent sibling used to answer for the whole builtin, so
    a command the user had just approved came back as a second prompt.
    """
    # Prepare
    _shell_with_a_second_source(monkeypatch)
    resolver, _ = _resolver(tmp_path, read_file={"permission": "ask"})
    outcome = await resolver.resolve("file_system.bash", {"command": "npm test"})
    assert [
        permission["sessionPattern"] for permission in outcome.required_permissions
    ] == ["npm test *"]
    await resolver.grant("file_system.bash", _granted(outcome), permanent=False)

    # Do
    again = await resolver.resolve("file_system.bash", {"command": "npm test"})

    # Assert
    assert again.decision == "allow"


@pytest.mark.asyncio
async def test_a_sibling_with_nothing_to_scope_does_not_widen_the_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: The same two-tool shell builtin.
    *Do*: Approve ``npm test``, answering every prompt the resolver raises for it.
    *Assert*: An unrelated destructive command still asks. The second prompt carried
    nothing to scope a grant to, so answering it recorded "always" against the shell
    itself — approving one command silently approved every command after it.
    """
    # Prepare
    _shell_with_a_second_source(monkeypatch)
    resolver, _ = _resolver(tmp_path, read_file={"permission": "ask"})

    # Do -- the UI answers whatever it is asked, up to a bounded number of rounds.
    for _ in range(2):
        outcome = await resolver.resolve("file_system.bash", {"command": "npm test"})
        if outcome.decision != "ask":
            break
        await resolver.grant("file_system.bash", _granted(outcome), permanent=False)

    # Assert
    other = await resolver.resolve("file_system.bash", {"command": "rm -rf /tmp/x"})
    assert other.decision == "ask"
    assert other.required_permissions


@pytest.mark.asyncio
async def test_a_sibling_that_allows_the_call_does_not_licence_a_blanket_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: The two-tool shell builtin, where the second tool clears the call
    outright and the shell itself cannot read it -- an empty command stands in for
    the two Windows shells' parsers disagreeing.
    *Do*: Run the prompt round trip, approving whatever is asked.
    *Assert*: An unrelated destructive command still asks. A tool that abstains is
    not a tool that asks: routing it to the bare ask offered the user a prompt whose
    "yes" was recorded as "always" against every shell, so one benign call bought a
    session-wide -- and, permanently, a config-persisted -- allow-all.
    """
    # Prepare
    _shell_with_a_second_source(monkeypatch)
    resolver, _ = _resolver(tmp_path, read_file={"permission": "always"})

    # Do
    for _ in range(2):
        outcome = await resolver.resolve("file_system.bash", {"command": ""})
        if outcome.decision != "ask":
            break
        await resolver.grant("file_system.bash", _granted(outcome), permanent=False)

    # Assert
    other = await resolver.resolve("file_system.bash", {"command": "rm -rf /tmp/x"})
    assert other.decision == "ask"
    assert other.required_permissions


@pytest.mark.asyncio
async def test_a_command_no_shell_can_read_is_scoped_to_itself(tmp_path: Path) -> None:
    """*Prepare*: A resolver, and a command the shell's splitter returns no parts
    for -- what the two Windows shells do to each other's commands, since they
    split with different parsers.
    *Do*: Approve it for the session, then resolve it again and a destructive one.
    *Assert*: The ask names the command, the repeat runs, and the destructive one
    still asks. The shell tool itself was the only other thing to grant, and
    ``_is_unconditionally_allowed`` reads a tool-wide "always" as "every command
    from here on" -- so one unreadable call would have bought an allow-all.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)

    # Do
    outcome = await resolver.resolve("file_system.bash", {"command": "&&"})
    await resolver.grant("file_system.bash", _granted(outcome), permanent=False)

    # Assert
    assert [
        permission["sessionPattern"] for permission in outcome.required_permissions
    ] == ["&&"]
    again = await resolver.resolve("file_system.bash", {"command": "&&"})
    assert again.decision == "allow"
    other = await resolver.resolve("file_system.bash", {"command": "rm -rf /tmp/x"})
    assert other.decision == "ask"
    assert other.required_permissions


@pytest.mark.asyncio
async def test_a_permanent_grant_for_an_unreadable_command_outlives_the_session(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and the same unreadable command approved for good.
    *Do*: Resolve it again against a fresh store, as the next session would.
    *Assert*: It runs. The shells match their allowlists against a *parsed*
    command, and this one parses to nothing, so only the resolver can read back
    the entry it wrote -- without that, "always" would prompt every session.
    """
    # Prepare
    resolver, orchestrator = _resolver(tmp_path)
    outcome = await resolver.resolve("file_system.bash", {"command": "&&"})
    await resolver.grant("file_system.bash", _granted(outcome), permanent=True)
    assert "&&" in orchestrator.config.tools["bash"]["allowlist"]

    # Do
    next_session, _ = _resolver(tmp_path, bash={"allowlist": ["&&"]})
    again = await next_session.resolve("file_system.bash", {"command": "&&"})

    # Assert
    assert again.decision == "allow"


@pytest.mark.parametrize("command", ["", "   "])
@pytest.mark.asyncio
async def test_a_blank_command_is_scoped_to_itself(
    tmp_path: Path, command: str
) -> None:
    """*Prepare*: A resolver, and a shell call whose command is blank.
    *Do*: Approve it for the session, then resolve it again and a destructive one.
    *Assert*: The ask names the command, the repeat runs, and the destructive one
    still asks. A blank command is unreadable the way ``&&`` is, and it used to be
    the one unreadable shell call that reached the tool-wide grant -- so a
    confirmation for a no-op bought an allow-all for every command after it.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)

    # Do
    outcome = await resolver.resolve("file_system.bash", {"command": command})
    await resolver.grant("file_system.bash", _granted(outcome), permanent=False)

    # Assert
    assert [
        permission["sessionPattern"] for permission in outcome.required_permissions
    ] == [command]
    again = await resolver.resolve("file_system.bash", {"command": command})
    assert again.decision == "allow"
    other = await resolver.resolve("file_system.bash", {"command": "rm -rf /tmp/x"})
    assert other.decision == "ask"
    assert other.required_permissions


@pytest.mark.asyncio
async def test_a_permanent_grant_for_a_blank_command_persists_nothing(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and a blank shell call approved for good.
    *Do*: Read back what reached the config.
    *Assert*: Neither an allowlist entry nor a tool permission. The session store
    covers the call the user answered for; the shells match an allowlist by
    prefix, so a blank entry there would be junk, and a persisted ``always``
    would flip the builtin's own mode to ``allow`` and retire the resolver --
    denylist checks and all -- in every later session.
    """
    # Prepare
    resolver, orchestrator = _resolver(tmp_path)
    outcome = await resolver.resolve("file_system.bash", {"command": ""})

    # Do
    await resolver.grant("file_system.bash", _granted(outcome), permanent=True)

    # Assert
    bash_config = orchestrator.config.tools.get("bash", {})
    assert "permission" not in bash_config
    assert "" not in bash_config.get("allowlist", [])
    again = await resolver.resolve("file_system.bash", {"command": ""})
    assert again.decision == "allow"


@pytest.mark.parametrize("arguments", [{}, {"command": None}, {"command": 7}])
@pytest.mark.asyncio
async def test_a_shell_call_carrying_no_command_grants_nothing(
    tmp_path: Path, arguments: dict[str, Any]
) -> None:
    """*Prepare*: A resolver, and shell arguments with no command in them to read.
    *Do*: Approve the call permanently -- the widest answer the UI offers.
    *Assert*: Nothing is recorded, in the session or in the config, and a later
    command still asks. There is no pattern to scope such a call to, and the shell
    tool is the wrong thing to widen instead: the call itself never runs, since the
    tool rejects these arguments, but the grant would have outlived it.
    """
    # Prepare
    resolver, orchestrator = _resolver(tmp_path)

    # Do
    outcome = await resolver.resolve("file_system.bash", arguments)
    assert outcome.decision == "ask"
    await resolver.grant("file_system.bash", _granted(outcome), permanent=True)

    # Assert
    assert resolver.store.get_tool_permission("bash") is None
    assert "permission" not in orchestrator.config.tools.get("bash", {})
    other = await resolver.resolve("file_system.bash", {"command": "rm -rf /tmp/x"})
    assert other.decision == "ask"
    assert other.required_permissions


@pytest.mark.asyncio
async def test_a_write_with_nothing_to_scope_to_grants_the_whole_tool(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and a write to an ordinary path inside the workspace.
    *Do*: Approve it for the session, then resolve a write to a different path.
    *Assert*: Both are allowed. An in-workspace write needs no permission scope, so
    there is nothing narrower than the tool to record — the fallback legacy takes.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)
    first = await resolver.resolve(
        "file_system.write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}
    )
    assert first.decision == "ask"
    assert first.required_permissions == ()

    # Do
    await resolver.grant("file_system.write_file", (), permanent=False)
    second = await resolver.resolve(
        "file_system.write_file", {"path": str(tmp_path / "b.txt"), "content": "y"}
    )

    # Assert
    assert second.decision == "allow"


@pytest.mark.asyncio
async def test_an_always_configured_write_still_asks_for_a_sensitive_file(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver whose ``write_file`` is configured ``always``, as
    accept-edits and an "always allow" answer both leave it.
    *Do*: Resolve an ordinary write and a write to ``.env``.
    *Assert*: The ordinary one runs unprompted and the sensitive one asks, scoped
    to that file. This is the split ``AgentLoop`` gives the same config, since it
    runs ``resolve_permission`` before it reads the tool's permission -- and the
    reason the builtin's mode has to stay ``ask``: an ``allow`` there retires the
    resolver, and the ``**/.env`` rule with it.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path, write_file={"permission": "always"})

    # Do
    ordinary = await resolver.resolve(
        "file_system.write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}
    )
    sensitive = await resolver.resolve(
        "file_system.write_file", {"path": str(tmp_path / ".env"), "content": "K=1"}
    )

    # Assert
    assert ordinary.decision == "allow"
    assert sensitive.decision == "ask"
    assert [permission["scope"] for permission in sensitive.required_permissions] == [
        PermissionScope.FILE_PATTERN.value
    ]


@pytest.mark.asyncio
async def test_an_always_configured_shell_still_refuses_a_denylisted_command(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver whose ``bash`` is configured ``always``.
    *Do*: Resolve an ordinary command and a denylisted one.
    *Assert*: The ordinary one runs and the denylisted one is refused. A shell
    set to ``always`` in the user's config reaches the Runtime as a mode, and a
    mode of ``allow`` would run ``rm -rf /`` off a denylist nothing consulted.
    """
    # Prepare
    resolver, _ = _resolver(
        tmp_path, bash={"permission": "always", "denylist": ["rm -rf /"]}
    )

    # Do
    ordinary = await resolver.resolve("file_system.bash", {"command": "npm test"})
    denylisted = await resolver.resolve("file_system.bash", {"command": "rm -rf /"})

    # Assert
    assert ordinary.decision == "allow"
    assert denylisted.decision == "deny"


@pytest.mark.asyncio
async def test_a_denylisted_command_is_refused_rather_than_asked_about(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver whose config denylists a command pattern.
    *Do*: Resolve a bash call matching it.
    *Assert*: The resolver refuses, carrying the tool's own reason. A denylist the
    user cannot click past is the point of a denylist; turning it into a prompt
    would let one Enter defeat it.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path, bash={"denylist": ["npm test"]})

    # Do
    outcome = await resolver.resolve("file_system.bash", {"command": "npm test"})

    # Assert
    assert outcome.decision == "deny"
    assert outcome.reason is not None
    assert "denylist" in outcome.reason


@pytest.mark.asyncio
async def test_a_permanent_grant_writes_the_pattern_to_the_allowlist(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and a bash call that asks with a command pattern.
    *Do*: Grant it permanently.
    *Assert*: The pattern lands in the tool's configured allowlist, so the next
    session starts already allowing it — and this one does too, from the store.
    """
    # Prepare
    resolver, orchestrator = _resolver(tmp_path)
    outcome = await resolver.resolve("file_system.bash", {"command": "npm test"})
    assert "npm test" not in orchestrator.config.tools.get("bash", {}).get(
        "allowlist", []
    )

    # Do
    await resolver.grant("file_system.bash", _granted(outcome), permanent=True)

    # Assert
    assert "npm test" in orchestrator.config.tools["bash"]["allowlist"]
    again = await resolver.resolve("file_system.bash", {"command": "npm test"})
    assert again.decision == "allow"


@pytest.mark.asyncio
async def test_a_permanent_grant_with_nothing_to_scope_to_writes_the_permission(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and an in-workspace write that asks with no scope.
    *Do*: Grant it permanently.
    *Assert*: The tool's own permission is persisted as always, which is the only
    thing there is to persist when no pattern narrows the call.
    """
    # Prepare
    resolver, orchestrator = _resolver(tmp_path)

    # Do
    await resolver.grant("file_system.write_file", (), permanent=True)

    # Assert
    assert orchestrator.config.tools["write_file"]["permission"] == (
        ToolPermission.ALWAYS.value
    )


@pytest.mark.asyncio
async def test_a_builtin_with_no_tool_behind_it_is_left_to_the_mode(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver over the ordinary catalogue.
    *Do*: Resolve a builtin name Vibe has no tool for.
    *Assert*: It asks. With no tool there is no rule to consult, and answering
    "allow" off an empty catalogue would run a call nothing vouched for.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)

    # Do
    outcome = await resolver.resolve("file_system.no_such_tool", {})

    # Assert
    assert outcome.decision == "ask"


def test_scope_names_survive_the_round_trip() -> None:
    """*Prepare*: Nothing; this pins the wire form the resolver publishes.
    *Do*: Read the scope value the callback carries.
    *Assert*: It matches what ``covers`` compares against. The permissions cross the
    Runtime as opaque JSON, so a rename on either side would silently stop matching.
    """
    assert PermissionScope.COMMAND_PATTERN.value == "command_pattern"
