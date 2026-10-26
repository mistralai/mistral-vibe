"""Approving a call on the Unified path has to mean what it means on the legacy one.

The Runtime's ``tool_modes`` grant per tool. Vibe grants per call — approving
``npm test`` says nothing about ``rm -rf``. These tests hold the bridge between
the two to the behaviour ``AgentLoop.approve_always`` already has.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from pathlib import Path
import sys
import tempfile
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
from vibe.app_server._provided_tools import (
    TODO_TOOL_NAME,
    VIBE_PROVIDED_TOOL_NAMES,
    VIBE_TOOL_GROUP,
)
from vibe.app_server._unified_permissions import (
    RUST_BUILTIN_TOOL_SOURCES,
    CommandEnvironment,
    ShellWrites,
    UnifiedPermissionResolver,
    git_bash_windows_path,
    shell_writes,
    windows_drive_path,
)


def _resolver(
    tmp_path: Path, routes: Mapping[str, str] | None = None, **tools: dict[str, Any]
) -> tuple[UnifiedPermissionResolver, FakeConfigOrchestrator[Any]]:
    """The bridge as ``build_unified_session_context`` assembles it.

    ``routes`` stands in for the MCP snapshot the adapter registers: Runtime
    route to the name Vibe published the tool under.
    """
    orchestrator = FakeConfigOrchestrator(build_test_vibe_config(tools=tools))
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=tmp_path,
        harness_files=HarnessFilesManager().for_session(tmp_path),
        permission_getter=store.get_tool_permission,
    )
    resolver = UnifiedPermissionResolver(manager, store, orchestrator)
    if routes:
        resolver.provided_names.register("mcp", routes)
    return resolver, orchestrator


def _shell_resolver(
    tmp_path: Path, command_environment: CommandEnvironment
) -> UnifiedPermissionResolver:
    """The bridge over the default catalogue for a session in one shell."""
    orchestrator = FakeConfigOrchestrator(build_test_vibe_config())
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=tmp_path,
        harness_files=HarnessFilesManager().for_session(tmp_path),
        permission_getter=store.get_tool_permission,
    )
    return UnifiedPermissionResolver(
        manager, store, orchestrator, command_environment=command_environment
    )


# The ids the harness mints: ``process-<hex>-<hex>`` over session and call ids.
_STARTED_PROCESS = f"process-{'a' * 12}-{'1' * 24}"
_OTHER_PROCESS = f"process-{'a' * 12}-{'2' * 24}"


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
async def test_a_permanent_grant_cannot_auto_allow_unreadable_shell_syntax(
    tmp_path: Path,
) -> None:
    """*Prepare*: Persist an unreadable command in the shell allowlist.
    *Do*: Resolve it again against a fresh store, as the next session would.
    *Assert*: It still asks. Invalid syntax must fail closed before allowlist
    matching because a real shell may execute behavior the parser omitted.
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
    assert again.decision == "ask"


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
async def test_a_never_configured_write_runs_only_where_its_allowlist_says(
    tmp_path: Path,
) -> None:
    """What ``_rust_tool_modes`` lifts this builtin to ``ask`` for, and what makes
    the lift safe: the mode says ``ask``, but the answer is still the configured
    refusal everywhere the list does not reach. ``.env`` is the case that would
    otherwise leak -- its rule returns ``ask``, and honouring that under a
    ``never`` would turn a refusal into a prompt.
    """
    resolver, _ = _resolver(
        tmp_path,
        write_file={
            "permission": "never",
            "allowlist": [str(tmp_path / "plans" / "*")],
        },
    )

    allowlisted = await resolver.resolve(
        "file_system.write_file",
        {"path": str(tmp_path / "plans" / "plan.md"), "content": "x"},
    )
    elsewhere = await resolver.resolve(
        "file_system.write_file", {"path": str(tmp_path / "src.py"), "content": "x"}
    )
    sensitive = await resolver.resolve(
        "file_system.write_file", {"path": str(tmp_path / ".env"), "content": "K=1"}
    )

    assert allowlisted.decision == "allow"
    assert elsewhere.decision == "deny"
    assert sensitive.decision == "deny"


@pytest.mark.asyncio
async def test_a_never_configured_write_denies_a_path_the_rules_cannot_read(
    tmp_path: Path,
) -> None:
    """The path rules raise on a path the OS cannot stat, and the Harness answers a
    resolver that raised with a prompt -- so the model could put a ``never`` tool in
    front of the user by naming a path that crashes the check.
    """
    resolver, _ = _resolver(
        tmp_path,
        write_file={
            "permission": "never",
            "allowlist": [str(tmp_path / "plans" / "*")],
        },
    )

    outcome = await resolver.resolve(
        "file_system.write_file", {"path": f"{tmp_path}/a\x00b", "content": "x"}
    )

    assert outcome.decision == "deny"


@pytest.mark.asyncio
async def test_an_always_configured_write_still_raises_a_path_the_rules_cannot_read(
    tmp_path: Path,
) -> None:
    """Only a ``never`` is decided here. Swallowing the failure for an ``always``
    tool would auto-allow the very call whose ``.env`` and denylist checks did not
    run; the Harness's own fallback prompts instead.
    """
    resolver, _ = _resolver(tmp_path, write_file={"permission": "always"})

    with pytest.raises(ValueError, match="null"):
        await resolver.resolve(
            "file_system.write_file", {"path": f"{tmp_path}/a\x00b", "content": "x"}
        )


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
async def test_a_grant_for_a_command_with_an_expansion_covers_the_next_one(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and a bash call whose argument is a variable.
    *Do*: Grant the ask, then resolve the same command with a different argument.
    *Assert*: It runs without asking. Recording the literal string instead made
    every one of these a single-use allow -- the shape that drives users to
    ``bypass_approval``.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)
    outcome = await resolver.resolve("file_system.bash", {"command": "npm test $SUITE"})
    assert [
        permission["sessionPattern"] for permission in outcome.required_permissions
    ] == ["npm test *"]
    await resolver.grant("file_system.bash", _granted(outcome), permanent=False)

    # Do
    again = await resolver.resolve("file_system.bash", {"command": "npm test $OTHER"})

    # Assert
    assert again.decision == "allow"


@pytest.mark.asyncio
async def test_a_grant_for_a_dynamic_program_name_covers_nothing_else(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and a bash call whose subcommand is a variable.
    *Do*: Grant the ask, then resolve a different subcommand of the same program.
    *Assert*: It asks again. ``git $SUB`` extracts to ``git``, so generalising it
    would mean ``git *`` -- one approval for every subcommand there is.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)
    outcome = await resolver.resolve("file_system.bash", {"command": "git $SUB"})
    assert [
        permission["sessionPattern"] for permission in outcome.required_permissions
    ] == ["git $SUB"]
    await resolver.grant("file_system.bash", _granted(outcome), permanent=False)

    # Do
    other = await resolver.resolve("file_system.bash", {"command": "git push --force"})

    # Assert
    assert other.decision == "ask"


@pytest.mark.asyncio
async def test_a_permanent_path_grant_is_written_to_the_shell_allowlist(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and a bash call reading a file outside the workspace.
    *Do*: Grant it permanently, then resolve the same call on a fresh session.
    *Assert*: The typed path grant is in the shell allowlist, and the next session
    allows the file without asking. The shell reads ``vibe-path`` entries back.
    """
    # Prepare
    resolver, orchestrator = _resolver(tmp_path)
    outside = Path("/etc/hosts")
    command = f"cat {outside}"
    outcome = await resolver.resolve("file_system.bash", {"command": command})
    assert [permission["scope"] for permission in outcome.required_permissions] == [
        PermissionScope.OUTSIDE_DIRECTORY.value
    ]
    [path_grant] = [
        str(permission["sessionPattern"]) for permission in outcome.required_permissions
    ]

    # Do
    await resolver.grant("file_system.bash", _granted(outcome), permanent=True)

    # Assert
    bash_config = orchestrator.config.tools["bash"]
    assert path_grant in bash_config["allowlist"]
    assert bash_config.get("permission") != ToolPermission.ALWAYS.value
    next_session, _ = _resolver(tmp_path, bash={"allowlist": bash_config["allowlist"]})
    again = await next_session.resolve("file_system.bash", {"command": command})
    assert again.decision == "allow"


@pytest.mark.asyncio
async def test_a_permanent_grant_persists_command_and_path_scopes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A resolver, and a bash call needing a command and an outside path.
    *Do*: Grant it permanently, then resolve it on a fresh session.
    *Assert*: Both patterns reach the allowlist, and the next session no longer
    asks for the outside path. A path grant is an entry the shell reads back.
    """
    # Prepare
    resolver, orchestrator = _resolver(tmp_path, bash={"allowlist": []})
    command = "grep $PATTERN /etc/hosts"
    outcome = await resolver.resolve("file_system.bash", {"command": command})
    assert sorted(
        str(permission["scope"]) for permission in outcome.required_permissions
    ) == [
        PermissionScope.COMMAND_PATTERN.value,
        PermissionScope.OUTSIDE_DIRECTORY.value,
    ]
    path_grant = next(
        str(permission["sessionPattern"])
        for permission in outcome.required_permissions
        if permission["scope"] == PermissionScope.OUTSIDE_DIRECTORY.value
    )

    # Do
    with caplog.at_level(logging.WARNING):
        await resolver.grant("file_system.bash", _granted(outcome), permanent=True)

    # Assert
    allowlist = orchestrator.config.tools["bash"]["allowlist"]
    assert allowlist == sorted(["grep", path_grant])
    assert PermissionScope.OUTSIDE_DIRECTORY.value not in caplog.text
    next_session, _ = _resolver(tmp_path, bash={"allowlist": allowlist})
    again = await next_session.resolve("file_system.bash", {"command": command})
    assert PermissionScope.OUTSIDE_DIRECTORY.value not in [
        permission["scope"] for permission in again.required_permissions
    ]


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


@pytest.mark.asyncio
async def test_a_provided_tool_asks_by_default(tmp_path: Path) -> None:
    """*Prepare*: A resolver with nothing configured for an MCP tool.
    *Do*: Resolve its route.
    *Assert*: It asks -- the default permission is the whole verdict.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path, {"mcp_linear.get_issue": "linear_get_issue"})

    # Do
    outcome = await resolver.resolve("mcp_linear.get_issue", {"id": "VIBE-1"})

    # Assert
    assert outcome.decision == "ask"
    assert outcome.required_permissions == ()


@pytest.mark.asyncio
async def test_an_always_configured_provided_tool_runs(tmp_path: Path) -> None:
    """*Prepare*: An MCP tool and a connector tool configured ``always``, under the
    names Vibe publishes them as, with both catalogues registered.
    *Do*: Resolve them by the Runtime routes the harness gates them under.
    *Assert*: Both allow, so the route reaches the configured name -- and one
    catalogue's registration did not displace the other's.
    """
    # Prepare
    resolver, _ = _resolver(
        tmp_path,
        {"mcp_linear.get_issue": "linear_get_issue"},
        **{
            "linear_get_issue": {"permission": "always"},
            "connector_github_create_issue": {"permission": "always"},
        },
    )
    resolver.provided_names.register(
        "connector", {"connector_github.create_issue": "connector_github_create_issue"}
    )

    # Do
    mcp = await resolver.resolve("mcp_linear.get_issue", {"id": "VIBE-1"})
    connector = await resolver.resolve("connector_github.create_issue", {})

    # Assert
    assert mcp.decision == "allow"
    assert connector.decision == "allow"


@pytest.mark.asyncio
async def test_a_never_configured_provided_tool_is_denied_not_asked(
    tmp_path: Path,
) -> None:
    """*Prepare*: An MCP tool whose remote name is not an identifier, so the
    Runtime routes it under a name that is not the one Vibe configured.
    *Do*: Resolve its route.
    *Assert*: It denies, naming the tool the way the user configured it. Deriving
    the name from the route instead would read an unwritten key and ask.
    """
    # Prepare
    resolver, _ = _resolver(
        tmp_path,
        {"mcp_github.create_pr": "github_create-pr"},
        **{"github_create-pr": {"permission": "never"}},
    )

    # Do
    outcome = await resolver.resolve("mcp_github.create_pr", {})

    # Assert
    assert outcome.decision == "deny"
    assert outcome.reason == "Tool 'github_create-pr' is permanently disabled"


@pytest.mark.asyncio
async def test_a_plugin_tool_group_is_configured_by_its_own_route(
    tmp_path: Path,
) -> None:
    """*Prepare*: A plugin's own provided tool configured ``never``.
    *Do*: Resolve its route.
    *Assert*: It denies -- a plugin group has no published name to translate to.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path, **{"deploy.ship_it": {"permission": "never"}})

    # Do
    outcome = await resolver.resolve("deploy.ship_it", {})

    # Assert
    assert outcome.decision == "deny"


@pytest.mark.asyncio
async def test_a_session_grant_covers_the_provided_tool_it_was_given_for(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver over the ordinary catalogue.
    *Do*: Grant one provided tool, then resolve it and a sibling route.
    *Assert*: The granted tool runs and the sibling still asks.
    """
    # Prepare
    resolver, _ = _resolver(
        tmp_path,
        {
            "mcp_linear.get_issue": "linear_get_issue",
            "mcp_linear.create_issue": "linear_create_issue",
        },
    )

    # Do
    ask = await resolver.resolve("mcp_linear.get_issue", {"id": "VIBE-1"})
    assert ask.decision == "ask"
    await resolver.grant("mcp_linear.get_issue", (), permanent=False)

    # Assert
    assert (
        await resolver.resolve("mcp_linear.get_issue", {"id": "VIBE-2"})
    ).decision == ("allow")
    assert (await resolver.resolve("mcp_linear.create_issue", {})).decision == "ask"


@pytest.mark.asyncio
async def test_a_permanent_provided_grant_writes_the_tool_permission(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver over the ordinary catalogue.
    *Do*: Grant a provided tool permanently.
    *Assert*: The permission lands under the published name, not the route.
    """
    # Prepare
    resolver, orchestrator = _resolver(
        tmp_path, {"mcp_linear.get_issue": "linear_get-issue"}
    )

    # Do
    await resolver.grant("mcp_linear.get_issue", (), permanent=True)

    # Assert
    assert orchestrator.config.tools["linear_get-issue"]["permission"] == (
        ToolPermission.ALWAYS.value
    )
    assert "mcp_linear.get_issue" not in orchestrator.config.tools


@pytest.mark.asyncio
async def test_an_unrouted_provided_tool_falls_back_to_its_route(
    tmp_path: Path,
) -> None:
    """*Prepare*: A registry holding one catalogue, re-registered without a route
    the previous revision published.
    *Do*: Resolve the dropped route and the surviving one.
    *Assert*: The dropped route reads its own name -- nothing configured there,
    so it asks -- while the surviving one still reaches its published name.
    """
    # Prepare
    resolver, _ = _resolver(
        tmp_path,
        {"mcp_github.create_pr": "github_create-pr", "mcp_github.list": "github_list"},
        **{
            "github_create-pr": {"permission": "never"},
            "github_list": {"permission": "never"},
        },
    )
    resolver.provided_names.register("mcp", {"mcp_github.list": "github_list"})

    # Do / Assert
    assert (await resolver.resolve("mcp_github.create_pr", {})).decision == "ask"
    assert (await resolver.resolve("mcp_github.list", {})).decision == "deny"


@pytest.mark.asyncio
async def test_the_unified_todo_tool_obeys_the_permission_configured_for_todo(
    tmp_path: Path,
) -> None:
    """*Prepare*: ``[tools.todo]`` set either way, with Vibe's own group registered.
    *Do*: Resolve the route the Runtime gates the tool under on the Unified path.
    *Assert*: The configured permission answers, as it does on the legacy harness.
    Nothing else reads ``vibe.todo``, so a missing registration would silently ask
    against a key the user never wrote.
    """
    # Prepare
    denied, _ = _resolver(tmp_path, **{"todo": {"permission": "never"}})
    allowed, _ = _resolver(tmp_path, **{"todo": {"permission": "always"}})
    for resolver in (denied, allowed):
        resolver.provided_names.register(VIBE_TOOL_GROUP, VIBE_PROVIDED_TOOL_NAMES)
    route = f"{VIBE_TOOL_GROUP}.{TODO_TOOL_NAME}"

    # Do / Assert
    assert (await denied.resolve(route, {"action": "read"})).decision == "deny"
    assert (await allowed.resolve(route, {"action": "read"})).decision == "allow"


@pytest.mark.asyncio
async def test_a_background_start_asks_scoped_to_the_command_it_would_run(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver over the default tool catalogue.
    *Do*: Resolve a ``process.start`` the user has not approved.
    *Assert*: It asks, scoped to the command the start would run. The start runs
    ``[shell, "-c", command]``, so it is the shell's own question, and the
    command is what a grant gets recorded against.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)

    # Do
    outcome = await resolver.resolve("process.start", {"command": "npm test"})

    # Assert
    assert outcome.decision == "ask"
    assert [
        permission["sessionPattern"] for permission in outcome.required_permissions
    ] == ["npm test *"]


@pytest.mark.asyncio
async def test_a_start_with_env_or_a_working_directory_never_auto_allows(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver whose ``bash`` allowlist already covers a command.
    *Do*: Resolve ``process.start`` for it -- once plain, once carrying env,
    once carrying a working directory.
    *Assert*: The plain start runs off the allowlist; the other two ask with no
    permissions attached. Env and cwd reach the tool unseen by any command
    rule, so an allowlist entry scoped to the command cannot speak for them --
    and the ask being permissionless means ``grant`` records nothing, so no
    answer widens past the call it was given.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path, bash={"allowlist": ["npm test"]})

    # Do
    plain = await resolver.resolve("process.start", {"command": "npm test"})
    with_env = await resolver.resolve(
        "process.start", {"command": "npm test", "env": {"LD_PRELOAD": "/tmp/x.so"}}
    )
    with_cwd = await resolver.resolve(
        "process.start", {"command": "npm test", "cwd": str(tmp_path / "elsewhere")}
    )

    # Assert
    assert plain.decision == "allow"
    assert with_env.decision == "ask"
    assert with_env.required_permissions == ()
    assert with_cwd.decision == "ask"
    assert with_cwd.required_permissions == ()


@pytest.mark.asyncio
async def test_a_start_from_the_tools_own_working_directory_stays_plain(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver whose ``bash`` allowlist already covers a command.
    *Do*: Resolve a ``process.start`` for it carrying a cwd equal to the tool's
    own working directory, then one carrying a different cwd.
    *Assert*: The tool's own cwd is where the tool already runs, so that start
    is the plain one and the allowlist answers for it; a cwd elsewhere is
    hidden from every command rule, so it asks with no permissions attached.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path, bash={"allowlist": ["npm test"]})

    # Do
    own_cwd = await resolver.resolve(
        "process.start", {"command": "npm test", "cwd": str(tmp_path)}
    )
    elsewhere = await resolver.resolve(
        "process.start", {"command": "npm test", "cwd": str(tmp_path / "elsewhere")}
    )

    # Assert
    assert own_cwd.decision == "allow"
    assert elsewhere.decision == "ask"
    assert elsewhere.required_permissions == ()


@pytest.mark.asyncio
async def test_a_denylisted_command_still_denies_a_start_with_env(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver whose config denylists a command pattern.
    *Do*: Resolve a ``process.start`` running it with env attached.
    *Assert*: It is refused. The env makes the call unscopeable, not unjudgeable:
    the command is still the command, and a denylist the user cannot click past
    is the point of a denylist.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path, bash={"denylist": ["rm -rf /"]})

    # Do
    outcome = await resolver.resolve(
        "process.start", {"command": "rm -rf /", "env": {"A": "1"}}
    )

    # Assert
    assert outcome.decision == "deny"
    assert outcome.reason is not None
    assert "denylist" in outcome.reason


@pytest.mark.asyncio
async def test_a_command_grant_does_not_cover_a_start_with_env(tmp_path: Path) -> None:
    """*Prepare*: A resolver with a session grant for one plain background start.
    *Do*: Resolve the same command again carrying env.
    *Assert*: It still asks. The grant was recorded against the command alone;
    letting it clear a call that also picks the process's environment would
    approve a start the user never described.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)
    outcome = await resolver.resolve("process.start", {"command": "npm test"})
    await resolver.grant("process.start", _granted(outcome), permanent=False)

    # Do
    again = await resolver.resolve("process.start", {"command": "npm test"})
    with_env = await resolver.resolve(
        "process.start", {"command": "npm test", "env": {"FOO": "1"}}
    )

    # Assert
    assert again.decision == "allow"
    assert with_env.decision == "ask"
    assert with_env.required_permissions == ()


@pytest.mark.asyncio
async def test_a_denylisted_command_is_refused_as_a_background_start(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver whose config denylists a command pattern.
    *Do*: Resolve a ``process.start`` running it.
    *Assert*: The resolver refuses. A background start executes the command with
    the shell, so the shell's denylist has to reach it -- the prompt this used to
    bypass was the whole hole.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path, bash={"denylist": ["rm -rf /"]})

    # Do
    outcome = await resolver.resolve("process.start", {"command": "rm -rf /"})

    # Assert
    assert outcome.decision == "deny"
    assert outcome.reason is not None
    assert "denylist" in outcome.reason


@pytest.mark.asyncio
async def test_a_session_grant_for_a_background_start_covers_only_its_command(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver that has just been told one start is approved.
    *Do*: Resolve the same command again, then a destructive one.
    *Assert*: The repeat runs and the other still asks. A start never granted
    more than its command on the legacy path; the unified path must not either.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)
    outcome = await resolver.resolve("process.start", {"command": "npm test"})
    await resolver.grant("process.start", _granted(outcome), permanent=False)

    # Do
    again = await resolver.resolve("process.start", {"command": "npm test --watch"})
    other = await resolver.resolve("process.start", {"command": "rm -rf /tmp/x"})

    # Assert
    assert again.decision == "allow"
    assert other.decision == "ask"
    assert other.required_permissions


@pytest.mark.asyncio
async def test_a_permanent_grant_for_a_background_start_lands_in_the_bash_allowlist(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and a ``process.start`` that asks with a command pattern.
    *Do*: Grant it permanently.
    *Assert*: The pattern lands in the shell's allowlist, so the next session
    starts the same command without a prompt. A start is a shell call, so the
    shell's allowlist is the honest place to remember it.
    """
    # Prepare
    resolver, orchestrator = _resolver(tmp_path)
    outcome = await resolver.resolve("process.start", {"command": "npm test"})

    # Do
    await resolver.grant("process.start", _granted(outcome), permanent=True)

    # Assert
    assert "npm test" in orchestrator.config.tools["bash"]["allowlist"]
    again = await resolver.resolve("process.start", {"command": "npm test"})
    assert again.decision == "allow"


@pytest.mark.asyncio
async def test_a_process_read_clears_without_a_prompt(tmp_path: Path) -> None:
    """*Prepare*: A resolver, and a process this session already started under the
    ``process.start`` gate.
    *Do*: Resolve ``process.output`` and ``process.list`` calls for it.
    *Assert*: Both run. Reading back a process the start gate let run adds no
    authority the call did not already have, and the reads carry nothing a
    prompt could scope a grant to.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)

    # Do
    output = await resolver.resolve("process.output", {"processId": "abc", "waitMs": 0})
    listing = await resolver.resolve("process.list", {})

    # Assert
    assert output.decision == "allow"
    assert listing.decision == "allow"


@pytest.mark.asyncio
async def test_a_process_stop_clears_without_a_prompt(tmp_path: Path) -> None:
    """*Prepare*: A resolver, and a process this session already started under the
    ``process.start`` gate.
    *Do*: Resolve a ``process.stop`` call for it.
    *Assert*: It runs. A stop only revokes a process the start gate already let
    run -- it cannot expand what the session was allowed to do, and gating it
    could strand a runaway process behind a prompt no human is watching.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)

    # Do
    stopped = await resolver.resolve("process.stop", {"processId": _STARTED_PROCESS})

    # Assert
    assert stopped.decision == "allow"


@pytest.mark.asyncio
async def test_a_process_write_asks_scoped_to_the_process_id(tmp_path: Path) -> None:
    """*Prepare*: A resolver, and a live process this session started.
    *Do*: Resolve a ``process.write`` to it, approve the ask, then resolve the
    same write and one to another process.
    *Assert*: The ask names the process id, the repeat runs, and the other still
    asks. A write mutates a live process, and the id is the only thing narrower
    than "every process" to scope a grant to.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)

    # Do
    outcome = await resolver.resolve(
        "process.write", {"processId": _STARTED_PROCESS, "text": "hello\n"}
    )
    await resolver.grant("process.write", _granted(outcome), permanent=False)
    again = await resolver.resolve(
        "process.write", {"processId": _STARTED_PROCESS, "text": "world\n"}
    )
    other = await resolver.resolve(
        "process.write", {"processId": _OTHER_PROCESS, "text": "hello\n"}
    )

    # Assert
    assert outcome.decision == "ask"
    assert [
        permission["sessionPattern"] for permission in outcome.required_permissions
    ] == [_STARTED_PROCESS]
    assert again.decision == "allow"
    assert other.decision == "ask"
    assert other.required_permissions


@pytest.mark.asyncio
async def test_a_permanent_grant_for_a_process_write_stays_in_the_session(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver, and a ``process.write`` approved "for good".
    *Do*: Read back what reached the config, then resolve the same write again.
    *Assert*: Nothing was persisted, and the session still covers the call. A
    grant scoped to a process id names an object that dies with the session, and
    the config layer has no place to record one -- the next session's ids are
    new, so remembering this one there would be junk that matches nothing.
    """
    # Prepare
    resolver, orchestrator = _resolver(tmp_path)
    outcome = await resolver.resolve(
        "process.write", {"processId": _STARTED_PROCESS, "text": "hello\n"}
    )
    assert outcome.decision == "ask"

    # Do
    await resolver.grant("process.write", _granted(outcome), permanent=True)

    # Assert
    bash_config = orchestrator.config.tools.get("bash", {})
    assert "permission" not in bash_config
    assert _STARTED_PROCESS not in bash_config.get("allowlist", [])
    again = await resolver.resolve(
        "process.write", {"processId": _STARTED_PROCESS, "text": "hello\n"}
    )
    assert again.decision == "allow"


@pytest.mark.asyncio
async def test_a_write_to_a_wildcard_process_id_grants_nothing(tmp_path: Path) -> None:
    """*Prepare*: A resolver, and a ``process.write`` whose process id is ``*``.
    *Do*: Resolve it, approve the ask for the session, then resolve a plain
    shell command and the same write again.
    *Assert*: The ask carries no permissions, and approving it records nothing
    that any later call matches. A grant is scoped by the process id, and the
    id reaches the resolver as model-supplied text: recording ``*`` as a
    command pattern on the shell would let one approval cover every command,
    so an id the harness would never mint has nothing to scope a grant to.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)

    # Do
    outcome = await resolver.resolve(
        "process.write", {"processId": "*", "text": "hello\n"}
    )
    assert outcome.decision == "ask"
    assert outcome.required_permissions == ()
    await resolver.grant("process.write", _granted(outcome), permanent=False)
    bash = await resolver.resolve("file_system.bash", {"command": "npm test"})
    again = await resolver.resolve(
        "process.write", {"processId": "*", "text": "hello\n"}
    )

    # Assert
    assert bash.decision == "ask"
    assert bash.required_permissions
    assert again.decision == "ask"


def test_scope_names_survive_the_round_trip() -> None:
    """*Prepare*: Nothing; this pins the wire form the resolver publishes.
    *Do*: Read the scope value the callback carries.
    *Assert*: It matches what ``covers`` compares against. The permissions cross the
    Runtime as opaque JSON, so a rename on either side would silently stop matching.
    """
    assert PermissionScope.COMMAND_PATTERN.value == "command_pattern"


@pytest.mark.asyncio
async def test_a_sandboxed_resolver_leaves_home_relative_paths_to_the_sandbox(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver whose paths name files in a sandbox.
    *Do*: Decide a write to a ``~/`` path and to a relative one.
    *Assert*: The ``~/`` path is authorized as written, for the sandbox to expand,
    instead of joined onto the cwd; the relative one is joined lexically.
    """
    # Prepare
    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(tools={"write_file": {"permission": "always"}})
    )
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=tmp_path,
        harness_files=HarnessFilesManager().for_session(tmp_path),
        permission_getter=store.get_tool_permission,
    )
    resolver = UnifiedPermissionResolver(
        manager, store, orchestrator, path_space="sandbox"
    )

    # Do
    home = await resolver.resolve(
        "file_system.write_file", {"path": "~/notes.txt", "content": "x"}
    )
    relative = await resolver.resolve(
        "file_system.write_file", {"path": "sub/../notes.txt", "content": "x"}
    )

    # Assert
    assert home.authorized_path == Path("~/notes.txt")
    assert relative.authorized_path == tmp_path / "notes.txt"


@pytest.mark.asyncio
async def test_a_shell_ask_carries_where_its_redirects_write(tmp_path: Path) -> None:
    """*Prepare*: A resolver over the default tool catalogue.
    *Do*: Resolve a bash call whose redirect hides behind a typed root name, as
    in ``<workspace>/abs/path``, next to a relative redirect and one from a root
    that names a variable.
    *Assert*: The ask carries the plainly spelled target, resolved, and the one
    only the shell can read, as written; not the relative one, which a ``cd``
    could move.
    """
    # Prepare
    resolver, _ = _resolver(tmp_path)
    agent = tmp_path / "Library" / "LaunchAgents" / "x.plist"

    # Do
    outcome = await resolver.resolve(
        "file_system.bash",
        {
            # Forward slashes: a backslash is an escape to the POSIX shells.
            "command": f"cat tools/plist.tmpl <workspace>{agent.as_posix()} "
            "2> err.log > /tmp/$NAME.log"
        },
    )

    # Assert
    assert outcome.decision == "ask"
    assert outcome.shell_write_paths == (agent.resolve(),)
    assert outcome.unplaced_shell_writes == ("/tmp/$NAME.log",)


@pytest.mark.asyncio
async def test_a_settled_or_non_shell_call_carries_no_write_paths(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resolver whose shells' allowlists cover a command.
    *Do*: Resolve that command, and a write-file call.
    *Assert*: Neither carries shell writes: an allowed call is never
    classified, and only a shell command has redirects.
    """
    # Prepare
    allowlist = {"allowlist": ["npm test"]}
    resolver, _ = _resolver(
        tmp_path, bash=allowlist, powershell=allowlist, git_bash=allowlist
    )

    # Do
    allowed = await resolver.resolve("file_system.bash", {"command": "npm test"})
    write = await resolver.resolve(
        "file_system.write_file", {"path": "a.txt", "content": "x"}
    )

    # Assert
    assert allowed.decision == "allow"
    assert (allowed.shell_write_paths, allowed.unplaced_shell_writes) == ((), ())
    assert (write.shell_write_paths, write.unplaced_shell_writes) == ((), ())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "builtin", "arguments", "unplaced"),
    [
        # PowerShell opens ``\tmp\out.log`` on whichever drive is current.
        ("powershell", "file_system.bash", {"command": "echo x > /tmp/out.log"}, 1),
        # Git Bash's ``/usr`` lies under its install directory.
        ("git_bash", "file_system.bash", {"command": "echo x > /usr/x"}, 1),
        # The process moves the directory Git Bash mounts at ``/tmp``.
        (
            "git_bash",
            "process.start",
            {"command": "echo x > /tmp/out.log", "env": {"TEMP": "C:\\keys"}},
            1,
        ),
        (
            "git_bash",
            "process.start",
            {"command": "echo x > /tmp/out.log", "env": {"TMPDIR": "C:\\keys"}},
            1,
        ),
        # A POSIX shell reads ``C:\x`` as the relative ``C:x``.
        ("unix", "file_system.bash", {"command": "echo x > C:\\x"}, 0),
    ],
)
async def test_a_command_is_read_by_the_shell_the_session_runs(
    tmp_path: Path,
    environment: CommandEnvironment,
    builtin: str,
    arguments: dict[str, Any],
    unplaced: int,
) -> None:
    """*Prepare*: A resolver for a session in one command environment.
    *Do*: Resolve a shell call whose redirect target that shell opens from a
    root it does not fix, or, for the POSIX shell, does not open from a root.
    *Assert*: The target is unplaced where that shell's reading leaves its
    file open, and not listed where that shell reads it as relative.
    """
    # Prepare
    resolver = _shell_resolver(tmp_path, environment)

    # Do
    outcome = await resolver.resolve(builtin, arguments)

    # Assert
    assert outcome.decision == "ask"
    assert outcome.shell_write_paths == ()
    assert len(outcome.unplaced_shell_writes) == unplaced


@pytest.mark.parametrize(
    ("target", "temp_moved", "expected"),
    [
        ("/c/Users/me/x.ps1", False, "C:/Users/me/x.ps1"),
        ("/d", False, "D:/"),
        ("/tmp/out.log", False, f"{tempfile.gettempdir()}/out.log"),
        ("/tmp/out.log", True, None),
        ("C:/Users/me/x.ps1", False, "C:/Users/me/x.ps1"),
        ("C:\\Users\\me\\x.ps1", False, "C:\\Users\\me\\x.ps1"),
        ("/usr/bin/x", False, None),
        ("/cd/x", False, None),
        ("//server/share/x", False, None),
        ("/c/work/../Users/me/x", False, None),
        ("C:x", False, None),
    ],
)
def test_git_bash_fixes_only_its_drive_and_temp_mounts(
    target: str, temp_moved: bool, expected: str | None
) -> None:
    """Git Bash mounts each drive at ``/<letter>`` and its ``/tmp`` at the
    temporary directory unless the process moves it. Any other POSIX path lies
    under a mount Vibe does not read, and ``..`` may cross one.
    """
    assert git_bash_windows_path(target, temp_moved=temp_moved) == expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("C:\\Users\\me\\x.ps1", "C:\\Users\\me\\x.ps1"),
        ("C:/Users/me/x.ps1", "C:/Users/me/x.ps1"),
        ("\\\\server\\share\\x.txt", "\\\\server\\share\\x.txt"),
        ("\\Users\\me\\x.ps1", None),
        ("/Users/me/x.ps1", None),
        ("C:x.ps1", None),
        ("\\\\?\\C:\\x", None),
        ("\\\\.\\pipe\\x", None),
    ],
)
def test_powershell_fixes_only_drive_and_share_rooted_paths(
    target: str, expected: str | None
) -> None:
    """``\\x`` and ``/x`` land on the drive current when the command runs, ``C:x``
    under that drive's current directory, and a device path skips the
    normalisation every other path goes through.
    """
    assert windows_drive_path(target) == expected


@pytest.mark.skipif(sys.platform != "win32", reason="needs this host's drives")
@pytest.mark.parametrize(
    ("environment", "command"),
    [
        ("powershell", "echo x > C:\\Users\\me\\x.ps1"),
        ("git_bash", "echo x > /c/Users/me/x.ps1"),
        ("git_bash", "echo x > 'C:\\Users\\me\\x.ps1'"),
    ],
)
def test_a_windows_shell_places_a_drive_rooted_write(
    environment: CommandEnvironment, command: str
) -> None:
    """Each Windows shell's spelling of the same drive path names one file."""
    writes = shell_writes(command, environment=environment)

    assert writes == ShellWrites(paths=(Path("C:/Users/me/x.ps1").resolve(),))


def test_sandbox_write_paths_are_normalised_without_this_hosts_links() -> None:
    """*Prepare*: A command writing an absolute path with ``..`` in a sandbox.
    *Do*: Read where it writes in the sandbox's path space.
    *Assert*: Collapsed lexically: the path names a file in the sandbox, which
    this host's file system does not hold.
    """
    # Do
    writes = shell_writes(
        "echo x > /work/repo/../etc/x", environment="unix", path_space="sandbox"
    )

    # Assert
    assert writes == ShellWrites(paths=(Path("/work/etc/x"),))


def _workdir_resolver(
    tmp_path: Path,
    *,
    workspace_roots: tuple[Path, ...] = (),
    config: Mapping[str, Any] | None = None,
    **tools: dict[str, Any],
) -> tuple[UnifiedPermissionResolver, FakeConfigOrchestrator[Any]]:
    """A resolver whose session sits in ``tmp_path/work``, so ``tmp_path`` is outside."""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(tools=tools, **(config or {}))
    )
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=work,
        harness_files=HarnessFilesManager().for_session(
            work, workspace_roots=list(workspace_roots)
        ),
        permission_getter=store.get_tool_permission,
    )
    return UnifiedPermissionResolver(manager, store, orchestrator), orchestrator


_DENY_OUTSIDE = {"outside_workdir": "deny"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("builtin", "arguments"),
    [
        ("file_system.read_file", {"path": "{outside}/secret.txt"}),
        ("file_system.write_file", {"path": "{outside}/secret.txt", "content": "x"}),
        (
            "file_system.edit",
            {"file_path": "{outside}/secret.txt", "old_string": "a", "new_string": "b"},
        ),
    ],
)
async def test_outside_workdir_deny_refuses_a_file_tool_call_outside_the_workdir(
    tmp_path: Path, builtin: str, arguments: dict[str, Any]
) -> None:
    resolver, _ = _workdir_resolver(tmp_path, config=_DENY_OUTSIDE)
    call = {
        key: value.format(outside=tmp_path) if isinstance(value, str) else value
        for key, value in arguments.items()
    }
    supplied = call.get("path") or call["file_path"]

    outcome = await resolver.resolve(builtin, call)

    assert outcome.decision == "deny"
    assert outcome.reason is not None
    assert supplied in outcome.reason
    assert "outside the working directory" in outcome.reason
    assert 'outside_workdir = "deny"' in outcome.reason


@pytest.mark.asyncio
async def test_outside_workdir_deny_names_the_supplied_path_not_a_link_target(
    tmp_path: Path,
) -> None:
    (tmp_path / "secret.txt").write_text("secret")
    try:
        (tmp_path / "work").mkdir()
        (tmp_path / "work" / "lnk").symlink_to(tmp_path / "secret.txt")
    except OSError:
        pytest.skip("this host cannot create symlinks")
    resolver, _ = _workdir_resolver(tmp_path, config=_DENY_OUTSIDE)

    outcome = await resolver.resolve(
        "file_system.read_file", {"path": str(tmp_path / "work" / "lnk")}
    )

    assert outcome.decision == "deny"
    assert outcome.reason is not None
    assert str(tmp_path / "work" / "lnk") in outcome.reason
    assert str(tmp_path / "secret.txt") not in outcome.reason


@pytest.mark.asyncio
async def test_outside_workdir_deny_keeps_file_tool_calls_inside_the_workdir(
    tmp_path: Path,
) -> None:
    resolver, _ = _workdir_resolver(tmp_path, config=_DENY_OUTSIDE)
    work = tmp_path / "work"

    read = await resolver.resolve(
        "file_system.read_file", {"path": str(work / "notes.md")}
    )
    sensitive = await resolver.resolve(
        "file_system.read_file", {"path": str(work / ".env")}
    )

    assert read.decision == "allow"
    assert sensitive.decision == "ask"


@pytest.mark.asyncio
async def test_outside_workdir_deny_refuses_an_always_configured_edit_outside(
    tmp_path: Path,
) -> None:
    resolver, _ = _workdir_resolver(
        tmp_path,
        config=_DENY_OUTSIDE,
        write_file={"permission": "always"},
        edit={"permission": "always"},
    )
    work = tmp_path / "work"

    inside = await resolver.resolve(
        "file_system.write_file", {"path": str(work / "a.txt"), "content": "x"}
    )
    outside = await resolver.resolve(
        "file_system.write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}
    )

    assert inside.decision == "allow"
    assert outside.decision == "deny"


@pytest.mark.asyncio
async def test_outside_workdir_access_still_asks_by_default(tmp_path: Path) -> None:
    resolver, _ = _workdir_resolver(tmp_path)

    read = await resolver.resolve(
        "file_system.read_file", {"path": str(tmp_path / "secret.txt")}
    )
    shell = await resolver.resolve("file_system.bash", {"command": "cat /etc/hosts"})

    assert read.decision == "ask"
    assert shell.decision == "ask"
    assert PermissionScope.OUTSIDE_DIRECTORY.value in [
        permission["scope"] for permission in shell.required_permissions
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("managed_shell", [False, True], ids=["legacy", "managed"])
@pytest.mark.parametrize(
    ("command", "denied_path"),
    [
        ("cat /etc/hosts", "/etc/hosts"),
        ("ls ../", "../"),
        # Git Bash spells a Windows drive as ``/<letter>/``.
        ("cat /c/Users/victim/secret.txt", "/c/Users/victim/secret.txt"),
        ("npm test && cat /etc/hosts", "/etc/hosts"),
    ],
)
async def test_outside_workdir_deny_refuses_a_shell_command_reaching_outside(
    tmp_path: Path, command: str, denied_path: str, managed_shell: bool
) -> None:
    resolver, _ = _workdir_resolver(
        tmp_path, config={**_DENY_OUTSIDE, "managed_shell_tools_enabled": managed_shell}
    )

    outcome = await resolver.resolve("file_system.bash", {"command": command})

    assert outcome.decision == "deny"
    assert outcome.reason is not None
    assert f"Access denied: {denied_path} is outside" in outcome.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("managed_shell", [False, True], ids=["legacy", "managed"])
@pytest.mark.parametrize(
    ("command", "link"), [("cat lnk", "lnk"), ("grep -r PRIVATE lnkdir", "lnkdir")]
)
async def test_outside_workdir_deny_refuses_a_bare_name_linking_outside(
    tmp_path: Path, command: str, link: str, managed_shell: bool
) -> None:
    secrets = tmp_path / "creds"
    secrets.mkdir()
    (secrets / "key.pem").write_text("PRIVATE")
    (tmp_path / "secret.txt").write_text("secret")
    work = tmp_path / "work"
    work.mkdir()
    try:
        (work / "lnk").symlink_to(tmp_path / "secret.txt")
        (work / "lnkdir").symlink_to(secrets, target_is_directory=True)
    except OSError:
        pytest.skip("this host cannot create symlinks")
    resolver, _ = _workdir_resolver(
        tmp_path, config={**_DENY_OUTSIDE, "managed_shell_tools_enabled": managed_shell}
    )

    outcome = await resolver.resolve("file_system.bash", {"command": command})

    assert outcome.decision == "deny"
    assert outcome.reason is not None
    assert f"Access denied: {link} is outside" in outcome.reason
    assert str(tmp_path.resolve()) not in outcome.reason


@pytest.mark.asyncio
async def test_outside_workdir_deny_still_asks_for_a_command_inside_the_workdir(
    tmp_path: Path,
) -> None:
    resolver, _ = _workdir_resolver(tmp_path, config=_DENY_OUTSIDE)

    outcome = await resolver.resolve("file_system.bash", {"command": "npm test"})

    assert outcome.decision == "ask"
    assert [
        permission["sessionPattern"] for permission in outcome.required_permissions
    ] == ["npm test *"]


@pytest.mark.asyncio
async def test_a_per_tool_outside_workdir_overrides_the_global_policy(
    tmp_path: Path,
) -> None:
    denies_bash_only, _ = _workdir_resolver(tmp_path, bash=_DENY_OUTSIDE)
    asks_for_reads_only, _ = _workdir_resolver(
        tmp_path, config=_DENY_OUTSIDE, read_file={"outside_workdir": "ask"}
    )
    outside_read = {"path": str(tmp_path / "secret.txt")}
    outside_command = {"command": "cat /etc/hosts"}

    assert (
        await denies_bash_only.resolve("file_system.bash", outside_command)
    ).decision == "deny"
    assert (
        await denies_bash_only.resolve("file_system.read_file", outside_read)
    ).decision == "ask"
    assert (
        await asks_for_reads_only.resolve("file_system.read_file", outside_read)
    ).decision == "ask"
    assert (
        await asks_for_reads_only.resolve("file_system.bash", outside_command)
    ).decision == "deny"


@pytest.mark.asyncio
async def test_outside_workdir_deny_honours_allowlisted_outside_paths(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    grant = f"vibe-path:directory_recursive:{shared.resolve()}"
    resolver, _ = _workdir_resolver(
        tmp_path,
        config=_DENY_OUTSIDE,
        read_file={"allowlist": [grant]},
        bash={"allowlist": ["cat", grant]},
    )

    read = await resolver.resolve(
        "file_system.read_file", {"path": str(shared / "nested" / "a.txt")}
    )
    shell = await resolver.resolve(
        "file_system.bash", {"command": f"cat {shared / 'a.txt'}"}
    )

    assert read.decision == "allow"
    assert shell.decision == "allow"


@pytest.mark.asyncio
async def test_outside_workdir_deny_honours_an_allowlisted_path_spelled_through_a_symlink(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    (shared / "sub").mkdir(parents=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(shared, target_is_directory=True)
    except OSError:
        pytest.skip("this host cannot create symlinks")
    grant = f"vibe-path:directory_recursive:{link / 'sub'}"
    resolver, _ = _workdir_resolver(
        tmp_path,
        config=_DENY_OUTSIDE,
        read_file={"allowlist": [grant]},
        bash={"allowlist": ["cat", grant]},
    )

    read = await resolver.resolve(
        "file_system.read_file", {"path": str(link / "sub" / "a.txt")}
    )
    shell = await resolver.resolve(
        "file_system.bash", {"command": f"cat {link / 'sub' / 'a.txt'}"}
    )

    assert read.decision == "allow"
    assert shell.decision == "allow"


@pytest.mark.asyncio
async def test_outside_workdir_deny_treats_added_directories_as_inside(
    tmp_path: Path,
) -> None:
    added = tmp_path / "added"
    added.mkdir()
    resolver, _ = _workdir_resolver(
        tmp_path, workspace_roots=(added,), config=_DENY_OUTSIDE
    )

    read = await resolver.resolve(
        "file_system.read_file", {"path": str(added / "a.txt")}
    )
    shell = await resolver.resolve(
        "file_system.bash", {"command": f"cat {added / 'a.txt'}"}
    )

    assert read.decision == "allow"
    assert shell.decision == "allow"


@pytest.mark.asyncio
async def test_outside_workdir_deny_outranks_an_earlier_session_grant(
    tmp_path: Path,
) -> None:
    resolver, orchestrator = _workdir_resolver(tmp_path)
    call = {"path": str(tmp_path / "secret.txt")}
    asked = await resolver.resolve("file_system.read_file", call)
    await resolver.grant("file_system.read_file", _granted(asked), permanent=False)
    assert (await resolver.resolve("file_system.read_file", call)).decision == "allow"

    await orchestrator.set_field("/outside_workdir", "deny")

    assert (await resolver.resolve("file_system.read_file", call)).decision == "deny"


@pytest.mark.asyncio
@pytest.mark.parametrize("managed_shell", [False, True], ids=["legacy", "managed"])
@pytest.mark.parametrize("policy", ["ask", "deny"])
async def test_an_always_shell_still_answers_for_paths_outside_the_workdir(
    tmp_path: Path, policy: str, managed_shell: bool
) -> None:
    resolver, _ = _workdir_resolver(
        tmp_path,
        config={
            "outside_workdir": policy,
            "managed_shell_tools_enabled": managed_shell,
        },
        bash={"permission": "always"},
    )

    outside = await resolver.resolve("file_system.bash", {"command": "cat /etc/hosts"})
    inside = await resolver.resolve("file_system.bash", {"command": "cat notes.md"})

    assert outside.decision == policy
    assert inside.decision == "allow"


@pytest.mark.asyncio
async def test_outside_workdir_deny_refuses_a_background_start_run_from_outside(
    tmp_path: Path,
) -> None:
    added = tmp_path / "added"
    added.mkdir()
    resolver, _ = _workdir_resolver(
        tmp_path, workspace_roots=(added,), config=_DENY_OUTSIDE
    )

    outside = await resolver.resolve(
        "process.start", {"command": "cat secret.txt", "cwd": str(tmp_path)}
    )
    in_added_dir = await resolver.resolve(
        "process.start", {"command": "cat a.txt", "cwd": str(added)}
    )

    assert outside.decision == "deny"
    assert outside.reason is not None
    assert str(tmp_path.resolve()) in outside.reason
    assert in_added_dir.decision == "ask"


@pytest.mark.asyncio
async def test_a_background_start_run_from_outside_still_asks_by_default(
    tmp_path: Path,
) -> None:
    resolver, _ = _workdir_resolver(tmp_path)

    outcome = await resolver.resolve(
        "process.start", {"command": "cat secret.txt", "cwd": str(tmp_path)}
    )

    assert outcome.decision == "ask"
    assert outcome.required_permissions == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("managed_shell", [False, True], ids=["legacy", "managed"])
async def test_an_always_shell_asks_only_for_the_outside_path_and_remembers_it(
    tmp_path: Path, managed_shell: bool
) -> None:
    resolver, _ = _workdir_resolver(
        tmp_path,
        config={"managed_shell_tools_enabled": managed_shell},
        bash={"permission": "always"},
    )
    call = {"command": f"cp {tmp_path / 'data.txt'} copy.txt"}

    asked = await resolver.resolve("file_system.bash", call)
    await resolver.grant("file_system.bash", _granted(asked), permanent=False)
    again = await resolver.resolve("file_system.bash", call)

    assert asked.decision == "ask"
    assert [permission["scope"] for permission in asked.required_permissions] == [
        PermissionScope.OUTSIDE_DIRECTORY.value
    ]
    assert again.decision == "allow"


@pytest.mark.asyncio
@pytest.mark.parametrize("managed_shell", [False, True], ids=["legacy", "managed"])
@pytest.mark.parametrize(
    "command", ["diff notes.md /dev/null", "cp /dev/null empty.txt", "cat /dev/stdin"]
)
async def test_outside_workdir_deny_lets_the_null_device_and_streams_through(
    tmp_path: Path, command: str, managed_shell: bool
) -> None:
    resolver, _ = _workdir_resolver(
        tmp_path, config={**_DENY_OUTSIDE, "managed_shell_tools_enabled": managed_shell}
    )

    outcome = await resolver.resolve("file_system.bash", {"command": command})

    assert outcome.decision != "deny"
