from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    from collections.abc import Callable, Mapping

    from mistralai_vibe_local_harness.vibe._permissions import PermissionResolver

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


def test_a_granted_tool_narrowed_by_an_allowlist_keeps_its_grant(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent grants bash outright but narrows it to a command list.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The ceiling still allows bash and nothing is reported: the list is
    per-call policy, which the child's own resolver applies, not the ceiling.
    """
    _write_agent(
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

    assert resolved.profiles[0].tool_ceiling["file_system.bash"] == "allow"
    assert resolved.issues == ()


def test_an_asked_tool_narrowed_by_a_denylist_stays_asked(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent asks for reads and denies a directory, in a session
    that reads anything unattended.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The ceiling asks, so the child's resolver is consulted for every
    read, and nothing is reported.
    """
    _write_agent(
        tmp_path,
        "reviewer",
        'description = "Reads the tree"\n'
        'agent_type = "subagent"\n'
        'enabled_tools = ["read_file"]\n'
        "[tools.read_file]\n"
        'permission = "ask"\n'
        'denylist = ["secret/*"]\n',
    )

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)),
        _tools(permission=ToolPermission.ALWAYS),
    )

    assert resolved.profiles[0].tool_ceiling["file_system.read_file"] == "ask"
    assert resolved.issues == ()


def test_each_profile_gets_a_resolver_built_from_its_own_overrides(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: Two subagents with different tool rules, and a catalogue that
    builds a resolver from a profile's overrides.
    *Do*: Resolve the workspace's agent types.
    *Assert*: Each agent type is handed the resolver built from its own file.
    """
    _write_agent(
        tmp_path,
        "reader",
        'description = "Reads"\n'
        'agent_type = "subagent"\n'
        "[tools.read_file]\n"
        'denylist = ["secret/*"]\n',
    )
    _write_agent(tmp_path, "reviewer", REVIEWER)
    built: dict[PermissionResolver, Mapping[str, Any]] = {}

    def resolver_for(overrides: Mapping[str, Any]) -> PermissionResolver:
        async def resolve(_name: str, _arguments: Mapping[str, Any]) -> Any:
            raise AssertionError("not called while resolving agent types")

        built[resolve] = overrides
        return resolve

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)),
        AgentToolCatalogue(
            available=frozenset({"read_file", "grep"}),
            permission_of=lambda _name: ToolPermission.ASK,
            permission_resolver=resolver_for,
        ),
    )

    reader = built[resolved.permission_resolvers["reader"]]
    reviewer = built[resolved.permission_resolvers["reviewer"]]
    assert reader["tools"]["read_file"]["denylist"] == ["secret/*"]
    assert reviewer["enabled_tools"] == ["read_file", "grep"]


def test_without_a_resolver_factory_no_profile_gets_a_resolver(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent, and a catalogue that cannot build resolvers.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The profile is bound with no resolver of its own, so its child
    keeps the parent's.
    """
    _write_agent(tmp_path, "reviewer", REVIEWER)

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert [profile.agent_type for profile in resolved.profiles] == ["reviewer"]
    assert resolved.permission_resolvers == {}


def test_a_refused_tool_with_an_allowlist_is_denied_without_a_report(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent refuses writes except for an allowlist.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The child is denied writes and nothing is reported: the profile
    already refuses the tool, so the list loosens nothing the child relies on.
    """
    _write_agent(
        tmp_path,
        "reviewer",
        'description = "Reads the tree"\n'
        'agent_type = "subagent"\n'
        'enabled_tools = ["write_file"]\n'
        "[tools.write_file]\n"
        'permission = "never"\n'
        'allowlist = ["notes/*"]\n',
    )

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    assert resolved.profiles[0].tool_ceiling["file_system.write_file"] == "deny"
    assert resolved.issues == ()


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


def test_the_skills_a_subagent_enables_reach_its_binding(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent enables one skill and disables another.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The binding carries both filters, so the Runtime narrows the child's skills.
    """
    _write_agent(
        tmp_path,
        "reviewer",
        REVIEWER + 'enabled_skills = ["alpha-skill"]\ndisabled_skills = ["bravo-*"]\n',
    )

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    profile = resolved.profiles[0]
    assert profile.enabled_skills == ["alpha-skill"]
    assert profile.disabled_skills == ["bravo-*"]
    assert profile.allows_skill("alpha-skill")
    assert not profile.allows_skill("charlie-skill")


def test_a_subagent_without_skill_filters_keeps_every_skill(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent that sets no skill filter.
    *Do*: Resolve the workspace's agent types.
    *Assert*: Its binding allows any skill, as the parent's catalogue holds it.
    """
    _write_agent(tmp_path, "reviewer", REVIEWER)

    resolved = resolve_agent_types(
        workspace_agent_types(manager_for(tmp_path)), _tools()
    )

    profile = resolved.profiles[0]
    assert profile.enabled_skills == []
    assert profile.disabled_skills == []
    assert profile.allows_skill("charlie-skill")


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


def test_a_tool_the_profile_disables_stays_denied_whatever_its_stanza(
    tmp_path: Path, manager_for: ManagerFor
) -> None:
    """*Prepare*: A subagent disables bash and still grants it in a stanza.
    *Do*: Resolve the workspace's agent types.
    *Assert*: The tool is denied and nothing is reported.
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
