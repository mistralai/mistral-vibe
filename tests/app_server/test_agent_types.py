from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.agents import AgentSafety, AgentType
from vibe.app_server._agent_types import (
    WORKSPACE_OWNER,
    AgentToolCatalogue,
    AgentTypeSource,
    resolve_agent_types,
    workspace_agent_types,
)
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import AgentProfile
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.tools.models import ToolPermission
from vibe.core.trusted_folders import trusted_folders_manager

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.usefixtures("config_dir")

REVIEWER = """\
description = "Reviews a diff and reports what it found"
agent_type = "subagent"
enabled_tools = ["read_file", "grep"]
"""


def _tools(
    *,
    available: tuple[str, ...] = ("read_file", "write_file", "bash", "skill"),
    permission: ToolPermission = ToolPermission.ASK,
) -> AgentToolCatalogue:
    return AgentToolCatalogue(
        available=frozenset(available), permission_of=lambda _name: permission
    )


type ManagerFor = Callable[[Path], AgentManager]


@pytest.fixture
def manager_for(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> ManagerFor:
    """An agent manager that discovers the agent files under a project root."""

    def build(project_root: Path) -> AgentManager:
        # Project agent files are only discovered under a trusted root, which is
        # what a real session has by the time it resolves its agent types.
        trusted_folders_manager.trust_for_session(project_root)
        harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
            project_root
        )
        return AgentManager(
            load_orchestrator(build_config()), harness_files=harness_files
        )

    return build


def _write_prompt(config_dir: Path, name: str, body: str) -> Path:
    prompts = config_dir / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    path = prompts / f"{name}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _write_agent(project_root: Path, name: str, body: str) -> Path:
    agents = project_root / ".vibe" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    path = agents / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_project_subagent_is_advertised_and_bound(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A project defines a subagent in ``.vibe/agents``.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The model is told the name, and the Host can spawn it.
    """
    path = _write_agent(tmp_path, "reviewer", REVIEWER)

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert [definition.name for definition in resolved.definitions] == ["reviewer"]
    assert [profile.agent_type for profile in resolved.profiles] == ["reviewer"]
    advertised = resolved.definitions[0]
    assert advertised.description == "Reviews a diff and reports what it found"
    assert advertised.path == str(path)
    assert resolved.profiles[0].profile_path == str(path)
    assert resolved.issues == ()


def test_the_builtin_explore_subagent_is_not_advertised(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A project defines no agent file at all.
    *Do*: Resolve the workspace's agent types.
    *Assert*: Nothing is advertised; the generic child already covers explore.
    """
    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.definitions == ()
    assert resolved.profiles == ()


def test_an_agent_file_that_shadows_explore_is_advertised(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A project defines its own ``explore.toml``.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The user's version is advertised, because they wrote it.
    """
    _write_agent(tmp_path, "explore", REVIEWER)

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert [definition.name for definition in resolved.definitions] == ["explore"]


def test_a_mode_is_not_offered_as_a_subagent(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A project defines an agent the user selects, not a subagent.
    *Do*: Resolve the workspace's agent types.
    *Assert*: It is not spawnable, which is the depth limit the legacy tool kept.
    """
    _write_agent(tmp_path, "careful", 'description = "A careful mode"\n')

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.definitions == ()


def test_a_named_prompt_becomes_the_prompt_the_child_runs_on(
    tmp_path: Path, config_dir: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent names a prompt file that exists.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The binding carries that file's text, because a child is handed a
    prompt rather than a prompt id.
    """
    _write_prompt(config_dir, "reviewing", "Report, never repair.")
    _write_agent(tmp_path, "reviewer", f'{REVIEWER}system_prompt_id = "reviewing"\n')

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.profiles[0].instructions == "Report, never repair."


def test_explicit_instructions_win_over_a_named_prompt(
    tmp_path: Path, config_dir: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent carries both an ``instructions`` string and a prompt id.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The string wins, as the more specific of the two.
    """
    _write_prompt(config_dir, "reviewing", "From the file.")
    _write_agent(
        tmp_path,
        "reviewer",
        f'{REVIEWER}system_prompt_id = "reviewing"\ninstructions = "Inline."\n',
    )

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.profiles[0].instructions == "Inline."


def test_a_subagent_with_no_prompt_inherits_the_parents(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent names no prompt of its own.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The binding carries no instructions, which the Host reads as
    "run on the parent's prompt".
    """
    _write_agent(tmp_path, "reviewer", REVIEWER)

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.profiles[0].instructions is None


def test_a_subagent_naming_a_missing_prompt_is_not_advertised(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent names a prompt file that does not exist.
    *Do*: Resolve the workspace's agent types.
    *Assert*: Nothing is advertised, so the model is never offered a name whose
    prompt cannot be read. Discovery rejects the file first, because the id it
    names has to resolve for the configuration it folds into to validate.
    """
    _write_agent(tmp_path, "reviewer", f'{REVIEWER}system_prompt_id = "absent"\n')

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.definitions == ()
    assert resolved.profiles == ()


def test_a_prompt_that_cannot_be_read_drops_the_agent_and_says_which_file(
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent reaches the resolver naming a prompt that is not there.
    *Do*: Resolve it.
    *Assert*: It is dropped from both halves with a message naming its file,
    rather than raising and taking every other agent's configuration with it.
    """
    path = tmp_path / "reviewer.toml"
    source = AgentTypeSource(
        name="reviewer",
        profile=AgentProfile(
            name="reviewer",
            display_name="Reviewer",
            description="Reviews a diff",
            safety=AgentSafety.SAFE,
            agent_type=AgentType.SUBAGENT,
            overrides={"system_prompt_id": "absent"},
            source_path=path,
        ),
        path=path,
        owner=WORKSPACE_OWNER,
        source_name=path.name,
    )

    resolved = resolve_agent_types([source], _tools())

    assert resolved.definitions == ()
    assert resolved.profiles == ()
    assert [issue.file for issue in resolved.issues] == [str(path)]
    assert "absent" in resolved.issues[0].message


def test_a_subagent_without_a_description_is_dropped_and_reported(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent file omits its description.
    *Do*: Resolve the workspace's agent types.
    *Assert*: It is dropped with a message, rather than invalidating the
    configuration every other agent shares.
    """
    path = _write_agent(tmp_path, "reviewer", 'agent_type = "subagent"\n')

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.definitions == ()
    assert [issue.file for issue in resolved.issues] == [str(path)]
    assert "description" in resolved.issues[0].message


def test_a_granted_tool_narrowed_by_an_allowlist_is_capped_at_ask(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent grants bash outright but narrows it to a command list.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The child has to ask, because a ceiling cannot carry the list, and
    the user is told the narrowing is approximated.
    """
    path = _write_agent(
        tmp_path,
        "reviewer",
        'description = "Reads the tree"\n'
        'agent_type = "subagent"\n'
        'enabled_tools = ["bash"]\n'
        "[tools.bash]\n"
        'permission = "always"\n'
        'allowlist = ["git"]\n',
    )

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.profiles[0].tool_ceiling["file_system.bash"] == "ask"
    assert [issue.file for issue in resolved.issues] == [str(path)]
    assert "bash" in resolved.issues[0].message


def test_a_granted_tool_with_no_list_stays_granted(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent grants bash outright and narrows nothing.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The ceiling still allows it, so the cap is the list and not the grant.
    """
    _write_agent(
        tmp_path,
        "reviewer",
        'description = "Reads the tree"\n'
        'agent_type = "subagent"\n'
        'enabled_tools = ["bash"]\n'
        "[tools.bash]\n"
        'permission = "always"\n',
    )

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.profiles[0].tool_ceiling["file_system.bash"] == "allow"
    assert resolved.issues == ()


def test_a_tool_the_profile_disables_is_denied_to_the_child(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent enables reading and nothing else.
    *Do*: Resolve the workspace's agent types.
    *Assert*: Everything it left out is denied, so the file is the ceiling.
    """
    _write_agent(tmp_path, "reviewer", REVIEWER)

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    ceiling = resolved.profiles[0].tool_ceiling
    assert ceiling["file_system.read_file"] == "ask"
    assert ceiling["file_system.write_file"] == "deny"
    assert ceiling["file_system.bash"] == "deny"


def test_a_name_an_installed_plugin_claims_is_refused(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A project agent uses a name a plugin already declares.
    *Do*: Resolve the workspace's agent types with that name reserved.
    *Assert*: Neither half is produced, and the user is told why. A plugin
    advertises inside its own capabilities, so letting the workspace bind the
    name would have the model read one owner's description and get the other's
    behaviour, and Core can reject the whole configuration over the duplicate.
    """
    path = _write_agent(tmp_path, "reviewer", REVIEWER)

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)),
        _tools(),
        reserved=frozenset({"reviewer"}),
    )

    assert resolved.definitions == ()
    assert resolved.profiles == ()
    assert [issue.file for issue in resolved.issues] == [str(path)]
    assert "plugin" in resolved.issues[0].message


def test_a_tool_the_profile_disables_is_not_reported_as_downgraded(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent disables bash and still carries a stanza for it.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The tool is denied and nothing is reported, because a message
    about a tool asking for approval describes a call the child cannot make.
    """
    _write_agent(
        tmp_path,
        "reviewer",
        'description = "Reads the tree"\n'
        'agent_type = "subagent"\n'
        'disabled_tools = ["bash"]\n'
        "[tools.bash]\n"
        'permission = "always"\n'
        'allowlist = ["git"]\n',
    )

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.profiles[0].tool_ceiling["file_system.bash"] == "deny"
    assert resolved.issues == ()


def test_the_advertised_half_alone_needs_no_tool_catalogue(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A project defines a subagent.
    *Do*: Resolve without a tool catalogue to measure ceilings against.
    *Assert*: The name is still advertised and nothing is bound, which is what a
    caller that only builds the prompt needs.
    """
    _write_agent(tmp_path, "reviewer", REVIEWER)

    resolved = resolve_agent_types(workspace_agent_types(manager_for(tmp_path)))

    assert [definition.name for definition in resolved.definitions] == ["reviewer"]
    assert resolved.profiles == ()
