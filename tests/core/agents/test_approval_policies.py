from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    BUILTIN_APPROVAL_POLICIES,
    ApprovalPolicyName,
    BuiltinAgentName,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.tools.models import ToolPermission
from vibe.core.trusted_folders import trusted_folders_manager


def _agents_dir(tmp_path: Path, **files: str) -> Path:
    directory = tmp_path / "agents"
    directory.mkdir(exist_ok=True)
    for stem, body in files.items():
        (directory / f"{stem.replace('_', '-')}.toml").write_text(
            body, encoding="utf-8"
        )
    return directory


TASK_LEAD = (
    'display_name = "Task Lead"\n'
    'safety = "destructive"\n'
    'instructions = "Lead the task."\n'
    '[tools.write_file]\npermission = "always"\n'
    '[tools.edit]\npermission = "always"\n'
)


async def _org_orchestrator(
    *, local: dict[str, Any] | None = None, policy: str = "", rollout: str | None = None
) -> ConfigOrchestrator[VibeConfigSchema]:
    growthbook = GrowthbookLayer()
    if rollout is not None:
        growthbook.set_variants({"vibe_cli_default_agent": rollout})
    admin = AdminConfigLayer()
    admin.load_managed_toml(policy)
    return await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[growthbook, OverridesLayer(data=local or {}), admin],
        default_layer_resolver=lambda: admin,
    )


def test_the_builtin_policies_are_the_four_permission_modes() -> None:
    assert set(BUILTIN_APPROVAL_POLICIES) == {
        "ask",
        "accept-edits",
        "smart-approve",
        "auto-approve",
    }
    assert set(BUILTIN_AGENTS) == {"default", "explore", "lean"}


class TestStart:
    def test_a_session_starts_on_the_default_agent_and_policy(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        agents = AgentManager(load_orchestrator(build_config()))

        assert agents.active_agent.name == BuiltinAgentName.DEFAULT
        assert agents.active_policy.name == ApprovalPolicyName.ACCEPT_EDITS

    @pytest.mark.parametrize("name", ["ask", "accept-edits", "auto-approve"])
    def test_an_agent_request_naming_a_policy_selects_the_policy(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
        name: str,
    ) -> None:
        agents = AgentManager(load_orchestrator(build_config()), initial_agent=name)

        assert agents.active_agent.name == BuiltinAgentName.DEFAULT
        assert agents.active_policy.name == name

    def test_a_default_agent_naming_a_policy_is_the_default_policy(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(default_agent="auto-approve")

        agents = AgentManager(load_orchestrator(config))

        assert agents.config.default_agent == BuiltinAgentName.DEFAULT
        assert agents.config.default_approval_policy == "auto-approve"
        assert agents.active_agent.name == BuiltinAgentName.DEFAULT
        assert agents.active_policy.name == "auto-approve"
        assert agents.config.bypass_tool_permissions is True

    def test_a_custom_agent_runs_the_default_policy_whatever_it_grants(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(
            agent_paths=[_agents_dir(tmp_path, task_lead=TASK_LEAD)],
            default_approval_policy="ask",
        )

        agents = AgentManager(load_orchestrator(config), initial_agent="task-lead")

        assert agents.active_agent.name == "task-lead"
        assert agents.active_agent.instructions == "Lead the task."
        assert agents.active_policy.name == ApprovalPolicyName.ASK
        assert agents.config.tools.get("edit", {}).get("permission") != "always"
        assert agents.config.tools.get("write_file", {}).get("permission") != "always"

    def test_an_agent_runs_the_requested_policy(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(agent_paths=[_agents_dir(tmp_path, task_lead=TASK_LEAD)])

        agents = AgentManager(
            load_orchestrator(config),
            initial_agent="task-lead",
            initial_policy="smart-approve",
        )

        assert agents.active_agent.name == "task-lead"
        assert agents.active_policy.name == ApprovalPolicyName.SMART_APPROVE

    def test_an_agent_cannot_name_its_policy(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        directory = _agents_dir(
            tmp_path, reviewer='approval_policy = "auto-approve"\nsafety = "yolo"\n'
        )
        config = build_config(agent_paths=[directory], default_approval_policy="ask")

        agents = AgentManager(load_orchestrator(config), initial_agent="reviewer")

        assert agents.active_policy.name == ApprovalPolicyName.ASK
        assert agents.config.bypass_tool_permissions is False

    def test_a_builtin_agent_runs_the_default_policy(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(
            installed_agents=["lean"], default_approval_policy="auto-approve"
        )

        agents = AgentManager(load_orchestrator(config), initial_agent="lean")

        assert agents.active_agent.name == BuiltinAgentName.LEAN
        assert agents.active_policy.name == ApprovalPolicyName.AUTO_APPROVE

    def test_an_agents_bypass_is_ignored(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        directory = _agents_dir(tmp_path, reckless="bypass_tool_permissions = true\n")
        config = build_config(agent_paths=[directory], default_approval_policy="ask")

        agents = AgentManager(load_orchestrator(config), initial_agent="reckless")

        assert agents.active_agent.name == "reckless"
        assert agents.active_policy.name == ApprovalPolicyName.ASK
        assert agents.config.bypass_tool_permissions is False


class TestAgentSettings:
    def test_an_agent_keeps_only_the_settings_an_agent_may_set(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        directory = _agents_dir(
            tmp_path,
            loose=(
                'instructions = "Be loose."\n'
                'safety = "yolo"\n'
                'approval_policy = "auto-approve"\n'
                "bypass_tool_permissions = true\n"
                "enable_auto_approve = true\n"
                'enabled_agents = ["loose"]\n'
                'enabled_tools = ["bash", "read_file", "edit"]\n'
                "[tools.bash]\n"
                'permission = "ask"\n'
                'allowlist = ["rm -rf"]\n'
                "sensitive_patterns = []\n"
                "[tools.edit]\n"
                'permission = "always"\n'
                "[tools.read_file]\n"
                'permission = "never"\n'
            ),
        )
        config = build_config(
            agent_paths=[directory],
            default_approval_policy="ask",
            tools={"bash": {"sensitive_patterns": ["rm"]}},
        )

        agents = AgentManager(load_orchestrator(config), initial_agent="loose")

        assert agents.active_agent.overrides == {
            "enabled_tools": ["bash", "read_file", "edit"],
            "tools": {
                "bash": {"permission": "ask"},
                "read_file": {"permission": "never"},
            },
        }
        assert agents.active_agent.instructions == "Be loose."
        assert [(i.code, i.name, i.key) for i in agents.agent_file_issues] == [
            ("agent.setting_ignored", "loose", "approval_policy"),
            ("agent.setting_ignored", "loose", "bypass_tool_permissions"),
            ("agent.setting_ignored", "loose", "enable_auto_approve"),
            ("agent.setting_ignored", "loose", "enabled_agents"),
            ("agent.setting_ignored", "loose", "safety"),
            ("agent.setting_ignored", "loose", "tools.bash.allowlist"),
            ("agent.setting_ignored", "loose", "tools.bash.sensitive_patterns"),
            ("agent.setting_ignored", "loose", "tools.edit.permission"),
        ]
        assert agents.config.tools["bash"]["sensitive_patterns"] == ["rm"]
        assert agents.config.tools["bash"].get("allowlist", []) == []
        assert agents.config.enabled_agents == []

    def test_a_policy_file_and_a_subagent_keep_their_settings(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        directory = _agents_dir(
            tmp_path,
            accept_edits='[tools.bash]\npermission = "always"\nallowlist = ["git"]\n',
            reviewer=(
                'description = "Reviews"\n'
                'agent_type = "subagent"\n'
                '[tools.bash]\npermission = "always"\nallowlist = ["git"]\n'
            ),
        )
        config = build_config(agent_paths=[directory])

        agents = AgentManager(load_orchestrator(config))

        assert agents.agent_file_issues == ()
        assert agents.config.tools["bash"]["allowlist"] == ["git"]
        (reviewer,) = agents.get_subagents()[-1:]
        assert reviewer.overrides["tools"]["bash"]["allowlist"] == ["git"]


class TestPolicyOverrideScope:
    def test_a_projects_policy_file_is_ignored(
        self,
        tmp_path: Path,
        config_dir: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        project = tmp_path / "repo"
        project_ask = project / ".vibe" / "agents" / "ask.toml"
        project_ask.parent.mkdir(parents=True)
        project_ask.write_text(
            'display_name = "Ask"\n[tools.bash]\npermission = "always"\n',
            encoding="utf-8",
        )
        user_edits = config_dir / "agents" / "accept-edits.toml"
        user_edits.parent.mkdir(parents=True, exist_ok=True)
        user_edits.write_text('display_name = "My Edits"\n', encoding="utf-8")
        trusted_folders_manager.trust_for_session(project)
        harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
            project
        )

        agents = AgentManager(
            load_orchestrator(build_config()),
            harness_files=harness_files,
            initial_policy="ask",
        )

        assert agents.active_policy is BUILTIN_APPROVAL_POLICIES["ask"]
        assert agents.config.tools.get("bash", {}).get("permission") != "always"
        assert agents.available_policies["accept-edits"].display_name == "My Edits"
        (issue,) = agents.agent_file_issues
        assert issue.code == "approval_policy.project_override_ignored"
        assert issue.name == "ask"
        assert issue.path == project_ask

    @pytest.mark.asyncio
    async def test_a_policy_file_on_a_projects_agent_path_is_ignored(
        self, tmp_path: Path
    ) -> None:
        directory = _agents_dir(tmp_path, auto_approve='display_name = "Mine"\n')
        project = OverridesLayer(
            data={"agent_paths": [str(directory)]}, name=ProjectConfigLayer.NAME
        )
        orchestrator = await ConfigOrchestrator.create(
            schema=VibeConfigSchema,
            layers=[project],
            default_layer_resolver=lambda: project,
        )

        agents = AgentManager(orchestrator, initial_policy="auto-approve")

        assert agents.active_policy is BUILTIN_APPROVAL_POLICIES["auto-approve"]
        assert [issue.code for issue in agents.agent_file_issues] == [
            "approval_policy.project_override_ignored"
        ]


class TestSwitching:
    def test_switching_the_policy_keeps_the_agent(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(agent_paths=[_agents_dir(tmp_path, task_lead=TASK_LEAD)])
        agents = AgentManager(load_orchestrator(config), initial_agent="task-lead")

        agents.switch_policy("auto-approve")

        assert agents.active_agent.name == "task-lead"
        assert agents.active_policy.name == "auto-approve"
        assert agents.config.bypass_tool_permissions is True

    def test_switching_the_agent_keeps_the_policy(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(agent_paths=[_agents_dir(tmp_path, task_lead=TASK_LEAD)])
        agents = AgentManager(load_orchestrator(config), initial_policy="ask")

        agents.switch_agent("task-lead")

        assert agents.active_agent.name == "task-lead"
        assert agents.active_policy.name == ApprovalPolicyName.ASK

    def test_a_policy_never_loosens_the_agents_own_permission(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        directory = _agents_dir(
            tmp_path, careful='[tools.edit]\npermission = "never"\n'
        )
        config = build_config(agent_paths=[directory])

        agents = AgentManager(
            load_orchestrator(config),
            initial_agent="careful",
            initial_policy="accept-edits",
        )

        assert agents.config.tools["edit"]["permission"] == ToolPermission.NEVER
        assert agents.config.tools["write_file"]["permission"] == "always"

    def test_auto_approve_keeps_the_tools_the_agent_set_to_never_off(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        directory = _agents_dir(
            tmp_path,
            careful=(
                '[tools.edit]\npermission = "never"\n'
                '[tools.write_file]\npermission = "never"\nallowlist = ["plans/**"]\n'
                '[tools.bash]\npermission = "ask"\n'
            ),
        )
        config = build_config(agent_paths=[directory])
        agents = AgentManager(
            load_orchestrator(config),
            initial_agent="careful",
            initial_policy="accept-edits",
        )

        agents.switch_policy("auto-approve")

        assert agents.config.bypass_tool_permissions is True
        assert {"edit", "write_file"} <= set(agents.config.disabled_tools)
        assert "bash" not in agents.config.disabled_tools

        agents.switch_policy("ask")

        assert "edit" not in agents.config.disabled_tools
        assert agents.config.tools["edit"]["permission"] == ToolPermission.NEVER

    def test_the_cycle_holds_policies_only(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(agent_paths=[_agents_dir(tmp_path, task_lead=TASK_LEAD)])

        agents = AgentManager(load_orchestrator(config))

        assert agents.get_policy_order() == ["ask", "accept-edits", "auto-approve"]
        assert [a.name for a in agents.primary_agents()] == ["default", "task-lead"]

    def test_a_policy_file_overrides_the_builtin_policy(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        directory = _agents_dir(
            tmp_path,
            accept_edits='display_name = "Edits"\n[tools.bash]\npermission = "always"\n',
        )
        config = build_config(agent_paths=[directory])

        agents = AgentManager(load_orchestrator(config))

        assert agents.active_policy.display_name == "Edits"
        assert "accept-edits" not in {a.name for a in agents.primary_agents()}
        assert agents.config.tools["bash"]["permission"] == "always"


class TestOrgLists:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("enabled", "policies", "agents"),
        [
            # A list of agents naming no policy allows only ask.
            (["task-lead"], ["ask"], ["task-lead"]),
            (["task-lead", "default"], ["ask"], ["default", "task-lead"]),
            # A list naming no agent leaves the default agent.
            (["ask", "accept-edits"], ["ask", "accept-edits"], ["default"]),
            # A list naming policies too keeps the default agent.
            (["task-lead", "ask"], ["ask"], ["default", "task-lead"]),
            # A list naming nothing it knows, a subagent or a typo, allows only
            # ask, as it allowed no mode before the split.
            (["explore"], ["ask"], ["default"]),
            (["tsak-lead"], ["ask"], ["default"]),
        ],
    )
    async def test_an_org_allowlist_bounds_both_lists(
        self, tmp_path: Path, enabled: list[str], policies: list[str], agents: list[str]
    ) -> None:
        directory = _agents_dir(tmp_path, task_lead=TASK_LEAD)
        orchestrator = await _org_orchestrator(
            local={"agent_paths": [str(directory)]},
            policy=f"enabled_agents = {enabled!r}\n".replace("'", '"'),
        )

        manager = AgentManager(orchestrator)

        assert manager.get_policy_order() == policies
        assert [a.name for a in manager.primary_agents()] == agents
        assert manager.active_policy.name in policies

    @pytest.mark.asyncio
    async def test_an_org_allowlist_of_agents_starts_on_one_of_them(
        self, tmp_path: Path
    ) -> None:
        directory = _agents_dir(tmp_path, reviewer='enabled_tools = ["read_file"]\n')
        orchestrator = await _org_orchestrator(
            local={"agent_paths": [str(directory)]},
            policy='enabled_agents = ["reviewer"]\n',
        )

        manager = AgentManager(orchestrator)

        assert manager.active_agent.name == "reviewer"
        assert manager.active_policy.name == ApprovalPolicyName.ASK
        with pytest.raises(ValueError, match="disabled by your organization"):
            manager.switch_agent("default")

    @pytest.mark.asyncio
    async def test_an_allowlisted_agent_runs_the_allowlisted_policy(
        self, tmp_path: Path
    ) -> None:
        directory = _agents_dir(tmp_path, task_lead=TASK_LEAD)
        orchestrator = await _org_orchestrator(
            local={"agent_paths": [str(directory)]},
            policy='enabled_agents = ["task-lead", "ask"]\n',
        )

        manager = AgentManager(orchestrator, initial_agent="task-lead")

        assert manager.active_agent.name == "task-lead"
        assert manager.active_policy.name == ApprovalPolicyName.ASK

    @pytest.mark.asyncio
    async def test_forbidding_auto_approve_removes_the_policy_not_the_agent(
        self, tmp_path: Path
    ) -> None:
        directory = _agents_dir(tmp_path, reckless='safety = "yolo"\n')
        orchestrator = await _org_orchestrator(
            local={"agent_paths": [str(directory)]},
            policy="enable_auto_approve = false\n",
        )

        manager = AgentManager(orchestrator, initial_agent="reckless")

        assert manager.active_agent.name == "reckless"
        assert manager.active_policy.name == ApprovalPolicyName.ACCEPT_EDITS
        assert "auto-approve" not in manager.get_policy_order()
        with pytest.raises(ValueError, match="disabled by your organization"):
            manager.switch_policy("auto-approve")

    @pytest.mark.asyncio
    async def test_the_rollout_sets_the_default_policy_below_the_users_choice(
        self,
    ) -> None:
        rolled = await _org_orchestrator(
            local={"smart_approve_available": True}, rollout="smart-approve"
        )
        chosen = await _org_orchestrator(
            local={"smart_approve_available": True, "default_agent": "ask"},
            rollout="smart-approve",
        )

        assert AgentManager(rolled).active_policy.name == "smart-approve"
        assert AgentManager(chosen).active_policy.name == "ask"


def test_a_users_allowlist_naming_no_policy_bounds_the_policies_too(
    tmp_path: Path,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    config = build_config(
        agent_paths=[_agents_dir(tmp_path, task_lead=TASK_LEAD)],
        enabled_agents=["task-lead"],
        default_agent="task-lead",
    )

    agents = AgentManager(load_orchestrator(config))

    assert agents.active_agent.name == "task-lead"
    assert agents.get_policy_order() == ["ask"]


def test_a_users_allowlist_of_a_pure_agent_starts_it_on_ask(
    tmp_path: Path,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    directory = _agents_dir(tmp_path, reviewer='enabled_tools = ["read_file"]\n')
    config = build_config(
        agent_paths=[directory], enabled_agents=["reviewer"], default_agent="reviewer"
    )

    agents = AgentManager(load_orchestrator(config))

    assert agents.active_agent.name == "reviewer"
    assert agents.active_policy.name == ApprovalPolicyName.ASK


def test_an_allowlist_naming_policies_and_an_agent_keeps_the_default_agent(
    tmp_path: Path,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    """A config from before the split limits the modes and adds one custom
    agent; it starts as it did, on the default agent in its policy.
    """
    config = build_config(
        agent_paths=[_agents_dir(tmp_path, task_lead=TASK_LEAD)],
        enabled_agents=["ask", "auto-approve", "task-lead"],
        default_agent="ask",
    )

    agents = AgentManager(load_orchestrator(config))

    assert agents.active_agent.name == BuiltinAgentName.DEFAULT
    assert agents.active_policy.name == ApprovalPolicyName.ASK
    assert [a.name for a in agents.primary_agents()] == ["default", "task-lead"]
    assert agents.get_policy_order() == ["ask", "auto-approve"]


@pytest.mark.parametrize("enabled", [["typo-agent"], ["explore"], ["nothing-*"]])
def test_an_allowlist_matching_no_agent_leaves_the_policies_alone(
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    enabled: list[str],
) -> None:
    agents = AgentManager(load_orchestrator(build_config(enabled_agents=enabled)))

    assert agents.active_agent.name == "default"
    assert agents.active_policy.name == ApprovalPolicyName.ACCEPT_EDITS
    assert agents.get_policy_order() == ["ask", "accept-edits", "auto-approve"]


class TestSmartApproveGate:
    def test_smart_approve_is_hidden_until_the_rollout(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        hidden = AgentManager(load_orchestrator(build_config()))
        shown = AgentManager(
            load_orchestrator(build_config(smart_approve_available=True))
        )

        assert "smart-approve" not in hidden.get_policy_order()
        assert "smart-approve" in shown.get_policy_order()

    def test_asking_for_smart_approve_keeps_it_offered(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        agents = AgentManager(
            load_orchestrator(build_config()), initial_policy="smart-approve"
        )

        agents.switch_policy("ask")

        assert "smart-approve" in agents.get_policy_order()
