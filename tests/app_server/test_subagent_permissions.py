"""A declared subagent's calls pass its profile's rules and its session's."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import tomllib
from typing import Any, Literal

import pytest
import tomli_w

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.core.config import VibeConfigSchema
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.permissions import PermissionStore
from vibe.permissions import RequiredPermission

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from mistralai_vibe_local_harness.vibe._permissions import PermissionOutcome
from vibe.app_server._subagent_permissions import SubagentPermissions, narrower
from vibe.app_server._unified_permissions import UnifiedPermissionResolver


def _permissions(
    tmp_path: Path, **tools: dict[str, Any]
) -> tuple[SubagentPermissions, UnifiedPermissionResolver]:
    """The session's resolver and the subagent half, as the session context builds them."""
    orchestrator = FakeConfigOrchestrator(build_test_vibe_config(tools=tools))
    store = PermissionStore()

    def tools_for(config: Callable[[], VibeConfigSchema]) -> ToolManager:
        return ToolManager(
            config,
            defer_mcp=True,
            cwd=tmp_path,
            harness_files=HarnessFilesManager().for_session(tmp_path),
            permission_getter=store.get_tool_permission,
        )

    session = UnifiedPermissionResolver(
        tools_for(lambda: orchestrator.config), store, orchestrator
    )
    return SubagentPermissions(session, orchestrator, tools_for), session


type Decision = Literal["allow", "ask", "deny"]


def _profile(**tools: dict[str, Any]) -> Mapping[str, Any]:
    return {"tools": tools}


def _scope(pattern: str) -> dict[str, Any]:
    return {"scope": "command_pattern", "invocation_pattern": pattern}


@pytest.mark.parametrize(
    ("session", "profile", "expected"),
    [
        ("allow", "allow", "allow"),
        ("allow", "ask", "ask"),
        ("ask", "allow", "ask"),
        ("allow", "deny", "deny"),
        ("deny", "allow", "deny"),
        ("ask", "deny", "deny"),
    ],
)
def test_the_stricter_answer_wins(
    session: Decision, profile: Decision, expected: Decision
) -> None:
    outcome = narrower(
        PermissionOutcome(decision=session), PermissionOutcome(decision=profile)
    )

    assert outcome.decision == expected


def test_a_refusal_keeps_the_reason_of_the_side_that_refused() -> None:
    outcome = narrower(
        PermissionOutcome(decision="allow"),
        PermissionOutcome(decision="deny", reason="matches the profile's denylist"),
    )

    assert outcome.reason == "matches the profile's denylist"


def test_two_prompts_ask_for_both_scopes_once() -> None:
    outcome = narrower(
        PermissionOutcome(
            decision="ask", required_permissions=(_scope("npm test"), _scope("ls"))
        ),
        PermissionOutcome(
            decision="ask", required_permissions=(_scope("ls"), _scope("rm x"))
        ),
    )

    assert outcome.required_permissions == (
        _scope("npm test"),
        _scope("ls"),
        _scope("rm x"),
    )


def test_a_prompt_for_the_whole_tool_covers_a_scoped_one() -> None:
    outcome = narrower(
        PermissionOutcome(decision="ask", required_permissions=(_scope("ls"),)),
        PermissionOutcome(decision="ask"),
    )

    assert outcome.required_permissions == ()


@pytest.mark.asyncio
async def test_a_profile_denylist_refuses_a_read_the_session_allows(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session that reads anything, and a profile that also reads
    anything but denies one directory.
    *Do*: Resolve a read inside that directory and one outside it.
    *Assert*: The first is refused and the second runs.
    """
    # Prepare
    permissions, _ = _permissions(tmp_path, read_file={"permission": "always"})
    resolve = permissions.resolver_for(
        _profile(
            read_file={"permission": "always", "denylist": [f"{tmp_path}/secret/*"]}
        )
    )

    # Do
    secret = await resolve(
        "file_system.read_file", {"path": str(tmp_path / "secret" / "token.txt")}
    )
    notes = await resolve("file_system.read_file", {"path": str(tmp_path / "notes")})

    # Assert
    assert secret.decision == "deny"
    assert notes.decision == "allow"


@pytest.mark.asyncio
async def test_a_profile_ask_prompts_for_a_tool_the_session_runs_unattended(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session that writes files unattended, and a profile that asks.
    *Do*: Resolve a write.
    *Assert*: It asks, so the profile's ``ask`` holds against the session's grant.
    """
    # Prepare
    permissions, _ = _permissions(tmp_path, write_file={"permission": "always"})
    resolve = permissions.resolver_for(_profile(write_file={"permission": "ask"}))

    # Do
    outcome = await resolve(
        "file_system.write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}
    )

    # Assert
    assert outcome.decision == "ask"


@pytest.mark.asyncio
async def test_a_profile_allowlist_runs_its_commands_and_asks_for_the_rest(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session that runs any command, and a profile that asks for
    commands except an allowlisted one.
    *Do*: Resolve the allowlisted command and another one.
    *Assert*: The first runs and the second asks.
    """
    # Prepare
    permissions, _ = _permissions(tmp_path, bash={"permission": "always"})
    resolve = permissions.resolver_for(
        _profile(bash={"permission": "ask", "allowlist": ["npm test"]})
    )

    # Do
    allowlisted = await resolve("file_system.bash", {"command": "npm test"})
    other = await resolve("file_system.bash", {"command": "make deploy"})

    # Assert
    assert allowlisted.decision == "allow"
    assert other.decision == "ask"


@pytest.mark.asyncio
async def test_a_profile_cannot_run_a_command_the_session_asks_for(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session that asks for a command, and a profile that allowlists it.
    *Do*: Resolve that command.
    *Assert*: It still asks. A profile narrows the session; it never grants past
    it, whoever wrote it.
    """
    # Prepare
    permissions, _ = _permissions(tmp_path)
    resolve = permissions.resolver_for(
        _profile(bash={"permission": "always", "allowlist": ["make deploy"]})
    )

    # Do
    outcome = await resolve("file_system.bash", {"command": "make deploy"})

    # Assert
    assert outcome.decision == "ask"


@pytest.mark.asyncio
async def test_a_grant_given_to_the_session_answers_the_subagent_too(
    tmp_path: Path,
) -> None:
    """*Prepare*: A subagent prompt for a command both sides ask about.
    *Do*: Grant it for the session through the session's resolver, as an approval
    does, and resolve it again.
    *Assert*: It runs: the subagent shares the session's memory of approvals.
    """
    # Prepare
    permissions, session = _permissions(tmp_path)
    resolve = permissions.resolver_for(_profile(bash={"permission": "ask"}))
    asked = await resolve("file_system.bash", {"command": "make deploy"})
    assert asked.decision == "ask"

    # Do
    await session.grant(
        "file_system.bash",
        tuple(
            RequiredPermission.model_validate(rp, by_alias=True, by_name=False)
            for rp in asked.required_permissions
        ),
        permanent=False,
    )
    again = await resolve("file_system.bash", {"command": "make deploy"})

    # Assert
    assert again.decision == "allow"


@pytest.mark.asyncio
async def test_a_reloaded_allowlist_reaches_a_subagent_that_already_made_a_call(
    tmp_path: Path, config_dir: Path
) -> None:
    """*Prepare*: A session that asks for commands, and a subagent whose profile
    leaves the command rules to the session and has already asked once.
    *Do*: Allowlist the command in the user config and reload it, as ``/reload``
    does, then resolve the same command again.
    *Assert*: The subagent's next call follows the reloaded allowlist.
    """
    # Prepare
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["tools"] = {"bash": {"permission": "ask"}}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    orchestrator = await build_default_orchestrator()
    store = PermissionStore()

    def tools_for(config: Callable[[], VibeConfigSchema]) -> ToolManager:
        return ToolManager(
            config,
            defer_mcp=True,
            cwd=tmp_path,
            harness_files=HarnessFilesManager().for_session(tmp_path),
            permission_getter=store.get_tool_permission,
        )

    session = UnifiedPermissionResolver(
        tools_for(lambda: orchestrator.config), store, orchestrator
    )
    resolve = SubagentPermissions(session, orchestrator, tools_for).resolver_for(
        _profile(bash={"denylist": ["rm -rf /"]})
    )
    before = await resolve("file_system.bash", {"command": "curl example.com"})
    assert before.decision == "ask"

    # Do
    config["tools"] = {"bash": {"permission": "ask", "allowlist": ["curl"]}}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    await orchestrator.reload()
    after = await resolve("file_system.bash", {"command": "curl example.com"})

    # Assert
    assert after.decision == "allow"
