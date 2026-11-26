from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.agents.manager import AgentManager, AgentSelectionError
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    BUILTIN_APPROVAL_POLICIES,
    EXPLORE,
    AgentProfile,
    AgentSafety,
    AgentType,
    ApprovalPolicyName,
    BuiltinAgentName,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.config.builder import InvalidConfigError
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator


class TestAgentProfile:
    def test_explore_agent_is_subagent(self) -> None:
        """Test that EXPLORE agent has SUBAGENT type."""
        assert EXPLORE.agent_type == AgentType.SUBAGENT

    def test_explore_agent_has_safe_safety(self) -> None:
        """Test that EXPLORE agent has SAFE safety level."""
        assert EXPLORE.safety == AgentSafety.SAFE

    def test_explore_agent_has_enabled_tools(self) -> None:
        """Test that EXPLORE agent has expected enabled tools."""
        enabled_tools = EXPLORE.overrides.get("enabled_tools", [])
        assert "grep" in enabled_tools
        assert "read_file" in enabled_tools
        assert "skill" in enabled_tools

    def test_a_profile_remembers_the_file_it_was_parsed_from(
        self, tmp_path: Path
    ) -> None:
        """The Unified Harness advertises a subagent with the path behind it."""
        path = tmp_path / "reviewer.toml"
        path.write_text('description = "Reviews"\n', encoding="utf-8")

        assert AgentProfile.from_toml(path).source_path == path

    @pytest.mark.parametrize("stem", ["main", "Main"])
    def test_main_is_reserved_for_hook_matching(
        self, tmp_path: Path, stem: str
    ) -> None:
        path = tmp_path / f"{stem}.toml"
        path.write_text('description = "Mine"\n', encoding="utf-8")

        with pytest.raises(ValueError, match="reserved for hook matching"):
            AgentProfile.from_toml(path)

    def test_a_builtin_profile_has_no_source_file(self) -> None:
        assert EXPLORE.source_path is None

    def test_builtin_agents_contains_explore(self) -> None:
        """Test that BUILTIN_AGENTS includes explore."""
        assert "explore" in BUILTIN_AGENTS
        assert BUILTIN_AGENTS["explore"] is EXPLORE


def _agents_dir(tmp_path: Path, **files: str) -> Path:
    directory = tmp_path / "agents"
    directory.mkdir(exist_ok=True)
    for stem, body in files.items():
        (directory / f"{stem.replace('_', '-')}.toml").write_text(
            body, encoding="utf-8"
        )
    return directory


class TestAgentManager:
    @pytest.fixture
    def manager(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> AgentManager:
        config = build_config()
        return AgentManager(load_orchestrator(config))

    def test_get_subagents_returns_only_subagents(self, manager: AgentManager) -> None:
        """Test that only SUBAGENT type agents are returned."""
        subagents = manager.get_subagents()

        for agent in subagents:
            assert agent.agent_type == AgentType.SUBAGENT

    def test_get_subagents_includes_explore(self, manager: AgentManager) -> None:
        """Test that EXPLORE is included in subagents."""
        subagents = manager.get_subagents()
        names = [a.name for a in subagents]

        assert "explore" in names

    def test_get_subagents_excludes_agents_and_policies(
        self, manager: AgentManager
    ) -> None:
        names = {a.name for a in manager.get_subagents()}

        assert BuiltinAgentName.DEFAULT not in names
        assert not names & set(BUILTIN_APPROVAL_POLICIES)

    def test_get_builtin_agent(self, manager: AgentManager) -> None:
        """Test getting a builtin agent by name."""
        agent = manager.get_agent("explore")

        assert agent is EXPLORE
        assert agent.agent_type == AgentType.SUBAGENT

    def test_get_nonexistent_agent_raises(self, manager: AgentManager) -> None:
        """Test that getting a nonexistent agent raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            manager.get_agent("nonexistent-agent")

    def test_ask_is_a_policy_not_an_agent(self, manager: AgentManager) -> None:
        policy = manager.get_policy("ask")

        assert policy.name == "ask"
        with pytest.raises(ValueError, match="Agent 'ask' not found"):
            manager.get_agent("ask")

    def test_smart_approve_as_default_policy_is_offered_without_the_flag(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(default_approval_policy="smart-approve")
        manager = AgentManager(load_orchestrator(config))

        assert manager.active_policy.name == "smart-approve"
        assert "smart-approve" in manager.available_policies

    def test_explicit_smart_approve_selection_bypasses_the_gate(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Explicitly selecting smart approve works even when the picker gate is off."""
        manager = AgentManager(
            load_orchestrator(build_config()), initial_policy="smart-approve"
        )

        assert manager.active_policy.name == "smart-approve"

    def test_explicit_smart_approve_stays_in_the_cycle(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """A --smart-approve start keeps the policy in the cycle after switching away.

        Regression: the mode was active at startup but dropped out of the picker
        once the user cycled off it, because it was hidden by the rollout gate.
        """
        manager = AgentManager(
            load_orchestrator(build_config()), initial_policy="smart-approve"
        )

        assert "smart-approve" in manager.available_policies
        assert "smart-approve" in manager.get_policy_order()

        order = manager.get_policy_order()
        current = manager.active_policy
        seen = [current.name]
        for _ in range(len(order)):
            current = manager.next_policy(current)
            seen.append(current.name)

        assert seen.count("smart-approve") >= 2
        assert current.name == "smart-approve"

    def test_initial_agent_rejects_subagent(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Test that creating AgentManager with a subagent as initial_agent raises."""
        config = build_config()
        with pytest.raises(ValueError, match="cannot be used as the primary agent"):
            AgentManager(load_orchestrator(config), initial_agent="explore")

    def test_initial_agent_accepts_subagent_when_allowed(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Test that allow_subagent=True permits subagent as initial_agent."""
        config = build_config()
        manager = AgentManager(
            load_orchestrator(config), initial_agent="explore", allow_subagent=True
        )
        assert manager.active_agent.name == "explore"

    def test_initial_agent_accepts_agent_type(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config()
        manager = AgentManager(
            load_orchestrator(config), initial_agent=BuiltinAgentName.DEFAULT
        )
        assert manager.active_agent.name == BuiltinAgentName.DEFAULT

    def test_initial_agent_raises_when_agent_is_disabled(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(
            agent_paths=[_agents_dir(tmp_path, reviewer="")],
            disabled_agents=["reviewer"],
        )
        with pytest.raises(ValueError, match="disabled_agents") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="reviewer")
        message = str(exc_info.value)
        assert "default_agent" not in message
        assert message.startswith("Agent 'reviewer'")

    def test_explicit_agent_excluded_by_enabled_agents_does_not_blame_default(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(
            agent_paths=[_agents_dir(tmp_path, reviewer="", writer="")],
            enabled_agents=["reviewer"],
        )
        with pytest.raises(ValueError, match="enabled_agents") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="writer")
        message = str(exc_info.value)
        assert "default_agent" not in message
        assert message.startswith("Agent 'writer'")

    @pytest.mark.parametrize(
        ("settings", "requested", "error"),
        [
            ({"disabled_agents": ["ask"]}, "ask", "disabled_agents"),
            ({"enabled_agents": ["ask"]}, "auto-approve", "enabled_agents"),
            (
                {"enable_auto_approve": False},
                "auto-approve",
                "auto-approve mode is disabled",
            ),
        ],
    )
    def test_a_requested_policy_the_users_config_excludes_stays_loud(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
        settings: dict[str, Any],
        requested: str,
        error: str,
    ) -> None:
        config = build_config(**settings)
        with pytest.raises(ValueError, match=error) as exc_info:
            AgentManager(load_orchestrator(config), initial_policy=requested)
        message = str(exc_info.value)
        assert "default_" not in message
        assert "admin-managed" not in message

    @pytest.mark.parametrize(
        ("settings", "requested", "error"),
        [
            (
                {"disabled_agents": ["ask"]},
                "ask",
                "Agent 'ask' is in 'disabled_agents'",
            ),
            (
                {"enabled_agents": ["ask", "accept-edits"]},
                "auto-approve",
                "Agent 'auto-approve' is not in 'enabled_agents'",
            ),
            (
                {"enable_auto_approve": False},
                "auto-approve",
                "auto-approve mode is disabled",
            ),
        ],
    )
    def test_switching_to_a_policy_the_users_config_excludes_explains_why(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
        settings: dict[str, Any],
        requested: str,
        error: str,
    ) -> None:
        manager = AgentManager(load_orchestrator(build_config(**settings)))

        with pytest.raises(ValueError, match=error) as exc_info:
            manager.switch_policy(requested)

        assert "admin-managed" not in str(exc_info.value)
        assert manager.active_policy.name == ApprovalPolicyName.ACCEPT_EDITS

    def test_initial_agent_raises_when_agent_does_not_exist(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config()
        with pytest.raises(ValueError, match="not found"):
            AgentManager(load_orchestrator(config), initial_agent="nonexistent-agent")

    def test_default_policy_excluded_by_enabled_agents_raises_config_contradiction(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["ask"])
        with pytest.raises(InvalidConfigError, match="enabled_agents") as exc_info:
            AgentManager(load_orchestrator(config))
        message = str(exc_info.value)
        assert "accept-edits" in message
        assert "default_approval_policy" in message

    def test_default_policy_excluded_by_disabled_agents_raises_config_contradiction(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(disabled_agents=["accept-edits"])
        with pytest.raises(InvalidConfigError, match="disabled_agents") as exc_info:
            AgentManager(load_orchestrator(config))
        assert "default_approval_policy" in str(exc_info.value)

    def test_disabled_agents_ignored_entirely_when_enabled_agents_set(
        self,
        caplog: pytest.LogCaptureFixture,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["ask"], disabled_agents=["ask"])
        with caplog.at_level("WARNING"):
            manager = AgentManager(load_orchestrator(config), initial_policy="ask")
        assert manager.active_policy.name == "ask"
        assert caplog.text == ""

    def test_disabling_auto_approve_mode_hides_the_policy(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """enable_auto_approve = false removes the yolo policy from the picker/cycle."""
        config = build_config(enable_auto_approve=False)
        manager = AgentManager(load_orchestrator(config))

        assert "auto-approve" not in manager.available_policies
        assert "auto-approve" not in manager.get_policy_order()

    def test_disabling_auto_approve_mode_wins_over_enabled_agents(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """The policy flag outranks enabled_agents, unlike disabled_agents."""
        config = build_config(
            enable_auto_approve=False,
            enabled_agents=["auto-approve", "ask", "accept-edits"],
        )
        manager = AgentManager(load_orchestrator(config))

        assert "auto-approve" not in manager.available_policies
        assert "ask" in manager.available_policies

    def test_a_custom_yolo_agent_runs_without_auto_approve_when_mode_disabled(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """The mode gate keys on the policy, so a yolo agent stays but never bypasses."""
        config = build_config(
            enable_auto_approve=False,
            agent_paths=[_agents_dir(tmp_path, reckless='safety = "yolo"\n')],
        )

        manager = AgentManager(load_orchestrator(config), initial_agent="reckless")

        assert "reckless" in manager.available_agents
        assert manager.active_agent.name == "reckless"
        assert manager.active_policy.name == ApprovalPolicyName.ACCEPT_EDITS
        assert manager.config.bypass_tool_permissions is False
        assert manager.excluded_agent is None

    @pytest.mark.parametrize(
        ("bash", "message"),
        [
            (
                "default_timeout = 600",
                "default_timeout is set without max_timeout_seconds.",
            ),
            (
                "max_timeout_seconds = 600",
                "max_timeout_seconds is set without default_timeout.",
            ),
        ],
    )
    def test_a_custom_agent_with_one_bash_timeout_gives_the_config_error(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
        bash: str,
        message: str,
    ) -> None:
        """*Prepare*: A custom agent file sets one of the two bash timeouts.
        *Do*: Start in it, then switch to it from another agent.
        *Assert*: Both give the main config's error naming the file, not "not
        found", and a refused switch leaves the running agent as it was.
        """
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        path = agents_dir / "slow.toml"
        path.write_text(f"[tools.bash]\n{bash}\n", encoding="utf-8")
        expected = (
            f"Agent 'slow' in {path}: tools: tools.bash: {message}"
            " Set both, or neither."
        )
        config = build_config(agent_paths=[agents_dir])

        with pytest.raises(InvalidConfigError) as started:
            AgentManager(load_orchestrator(config), initial_agent="slow")
        assert str(started.value) == expected

        manager = AgentManager(load_orchestrator(config))
        assert "slow" in manager.available_agents
        with pytest.raises(InvalidConfigError) as switched:
            manager.switch_agent("slow")
        assert str(switched.value) == expected
        assert manager.active_agent.name == BuiltinAgentName.DEFAULT
        assert "default_timeout" not in manager.config.tools.get("bash", {})

    @pytest.mark.parametrize(
        ("stem", "kind"),
        [("default", "builtin agent"), ("accept-edits", "approval policy")],
    )
    def test_a_broken_override_of_a_builtin_keeps_the_builtin(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
        stem: str,
        kind: str,
    ) -> None:
        """*Prepare*: agents/<builtin>.toml sets only a bash default above
        Vibe's maximum.
        *Do*: Start a session on the default agent and policy.
        *Assert*: The session starts on the builtins, and the ignored file is
        logged with its config error.
        """
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        path = agents_dir / f"{stem}.toml"
        path.write_text("[tools.bash]\ndefault_timeout = 600\n", encoding="utf-8")
        config = build_config(agent_paths=[agents_dir])

        with caplog.at_level("WARNING"):
            manager = AgentManager(load_orchestrator(config))

        assert manager.active_agent is BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        assert (
            manager.active_policy
            is BUILTIN_APPROVAL_POLICIES[ApprovalPolicyName.ACCEPT_EDITS]
        )
        assert any(
            f"override of {kind} '{stem}'" in record.getMessage()
            and str(path) in record.getMessage()
            and "default_timeout is set without max_timeout_seconds"
            in record.getMessage()
            for record in caplog.records
        )

    def test_an_unknown_tool_permission_is_ignored_in_an_agent_and_breaks_a_policy(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for stem in ("custom", "accept-edits"):
            (agents_dir / f"{stem}.toml").write_text(
                '[tools.bash]\npermission = "auto"\n', encoding="utf-8"
            )
        config = build_config(agent_paths=[agents_dir])

        with caplog.at_level("WARNING"):
            manager = AgentManager(load_orchestrator(config), initial_agent="custom")

        assert manager.active_agent.name == "custom"
        assert "permission" not in manager.config.tools.get("bash", {})
        assert (
            manager.active_policy
            is BUILTIN_APPROVAL_POLICIES[ApprovalPolicyName.ACCEPT_EDITS]
        )
        assert any(
            "override of approval policy 'accept-edits'" in record.getMessage()
            and "permission 'auto' is not one of" in record.getMessage()
            for record in caplog.records
        )

    def test_removed_plan_default_agent_starts_on_ask(
        self,
        caplog: pytest.LogCaptureFixture,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(default_agent="plan")
        with caplog.at_level("WARNING"):
            orchestrator = load_orchestrator(config)
            manager = AgentManager(orchestrator, initial_agent=None)

        assert manager.active_agent.name == BuiltinAgentName.DEFAULT
        assert manager.active_policy.name == ApprovalPolicyName.ASK
        assert "plan" not in manager.available_agents
        assert "plan" not in manager.available_policies
        assert "default_agent 'plan' is no longer a builtin agent" in caplog.text

    def test_explicit_plan_agent_is_rejected_with_its_replacement(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        with pytest.raises(
            AgentSelectionError, match="Agent 'plan' was removed in Vibe 3.0. Use 'ask'"
        ):
            AgentManager(load_orchestrator(build_config()), initial_agent="plan")

    def test_switching_to_plan_is_rejected_with_its_replacement(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        manager = AgentManager(load_orchestrator(build_config()))

        with pytest.raises(
            AgentSelectionError, match="Agent 'plan' was removed in Vibe 3.0. Use 'ask'"
        ):
            manager.switch_policy("plan")

    def test_custom_plan_agent_keeps_serving_as_default_agent(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "plan.toml").write_text(
            'display_name = "My Plan"\n', encoding="utf-8"
        )
        config = build_config(default_agent="plan", agent_paths=[agents_dir])

        orchestrator = load_orchestrator(config)
        manager = AgentManager(orchestrator, initial_agent=None)

        assert manager.active_agent.name == "plan"
        assert manager.active_agent.display_name == "My Plan"

    def test_an_agent_file_named_main_is_skipped(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "main.toml").write_text(
            'description = "Mine"\n', encoding="utf-8"
        )
        (agents_dir / "reviewer.toml").write_text(
            'description = "Reviews"\n', encoding="utf-8"
        )
        config = build_config(agent_paths=[agents_dir])

        with pytest.raises(ValueError, match="Agent 'main' not found"):
            AgentManager(load_orchestrator(config), initial_agent="main")
        manager = AgentManager(load_orchestrator(config), initial_agent="reviewer")
        assert manager.active_agent.name == "reviewer"

    def test_custom_agent_listing_exit_plan_mode_still_loads(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "legacy.toml").write_text(
            'enabled_tools = ["read_file", "exit_plan_mode"]\n'
            'disabled_tools = ["exit_plan_mode"]\n',
            encoding="utf-8",
        )
        config = build_config(agent_paths=[agents_dir])

        manager = AgentManager(load_orchestrator(config), initial_agent="legacy")

        assert manager.active_agent.name == "legacy"

    def test_install_required_agent_reports_install_not_disabled_agents(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        # 'lean' is install_required and enabled but not installed: the message
        # must point to installation, not blame disabled_agents.
        config = build_config(enabled_agents=["lean"])
        with pytest.raises(ValueError, match="requires installation") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="lean")
        assert "disabled_agents" not in str(exc_info.value)


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local", "policy", "rollout", "expected"),
    [
        ({}, "", None, ApprovalPolicyName.ACCEPT_EDITS),
        ({"default_approval_policy": "ask"}, "", None, ApprovalPolicyName.ASK),
        # The rollout only replaces the built-in default.
        ({}, "", "smart-approve", ApprovalPolicyName.SMART_APPROVE),
        ({}, "", "ask", ApprovalPolicyName.ASK),
        (
            {"default_approval_policy": "ask"},
            "",
            "smart-approve",
            ApprovalPolicyName.ASK,
        ),
        (
            {"default_approval_policy": "auto-approve"},
            "",
            "smart-approve",
            ApprovalPolicyName.AUTO_APPROVE,
        ),
        # The org's default beats both.
        (
            {},
            'default_approval_policy = "ask"\n',
            "smart-approve",
            ApprovalPolicyName.ASK,
        ),
        ({}, 'theme = "nord"\n', "smart-approve", ApprovalPolicyName.SMART_APPROVE),
    ],
)
async def test_a_new_session_starts_in_the_default_policy(
    local: dict[str, Any], policy: str, rollout: str | None, expected: str
) -> None:
    local = {**local, "smart_approve_available": True}
    orchestrator = await _org_orchestrator(local=local, policy=policy, rollout=rollout)

    agents = AgentManager(orchestrator)

    assert agents.active_agent.name == BuiltinAgentName.DEFAULT
    assert agents.active_policy.name == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local", "rollout"),
    [
        ({"disabled_agents": ["ask"]}, "ask"),
        # The experiment does not opt a user into smart-approve: the policy
        # still needs the vibe_cli_smart_approve rollout, and the user's lists.
        ({}, "smart-approve"),
        (
            {"smart_approve_available": True, "disabled_agents": ["smart-approve"]},
            "smart-approve",
        ),
    ],
)
async def test_a_rollout_default_the_session_cannot_start_starts_another_policy(
    local: dict[str, Any], rollout: str
) -> None:
    # The user did not choose the rollout's policy, so it must not stop the
    # session from starting.
    orchestrator = await _org_orchestrator(local=local, rollout=rollout)

    agents = AgentManager(orchestrator)

    assert agents.active_policy.name == ApprovalPolicyName.ACCEPT_EDITS
    assert agents.excluded_agent is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "requested", "started"),
    [
        # The user's own default, excluded by the org: a builtin policy.
        ('disabled_agents = ["auto-approve"]\n', None, "accept-edits"),
        # A requested policy the org excludes: a builtin policy.
        ('disabled_agents = ["auto-approve"]\n', "auto-approve", "accept-edits"),
        # ... or the org's own default, when it enforces one.
        (
            'default_approval_policy = "ask"\ndisabled_agents = ["auto-approve"]\n',
            "auto-approve",
            "ask",
        ),
        (
            'default_approval_policy = "ask"\nenabled_agents = ["ask", "accept-edits"]\n',
            "auto-approve",
            "ask",
        ),
    ],
)
async def test_a_policy_the_org_excludes_starts_another_policy(
    policy: str, requested: str | None, started: str
) -> None:
    orchestrator = await _org_orchestrator(
        local={"default_approval_policy": "auto-approve"}, policy=policy
    )

    agents = AgentManager(orchestrator, initial_policy=requested)

    assert agents.active_policy.name == started
    assert agents.excluded_agent == requested


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [None, pytest.param("auto-approve")])
async def test_an_enforced_plan_default_runs_as_ask_under_agent_lists(
    requested: str | None,
) -> None:
    # "plan" starts as ask, which the org's list allows, whether it is the
    # starting policy or the fallback for an excluded policy request.
    orchestrator = await _org_orchestrator(
        policy='default_agent = "plan"\nenabled_agents = ["ask", "accept-edits"]\n'
    )

    agents = AgentManager(orchestrator, initial_policy=requested)

    assert agents.active_policy.name == ApprovalPolicyName.ASK


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy", ['disabled_agents = ["ask"]\n', 'enabled_agents = ["accept-edits"]\n']
)
async def test_a_saved_plan_default_the_org_excludes_as_ask_starts_another_policy(
    policy: str,
) -> None:
    # A pre-3.0 saved "plan" starts as ask: the org's lists are checked on ask.
    orchestrator = await _org_orchestrator(
        local={"default_agent": "plan"}, policy=policy
    )

    agents = AgentManager(orchestrator)

    assert agents.active_policy.name == ApprovalPolicyName.ACCEPT_EDITS
    assert agents.excluded_agent is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local", "requested"),
    [({"default_approval_policy": "auto-approve"}, None), ({}, "auto-approve")],
)
async def test_an_org_that_forbids_auto_approve_starts_another_policy(
    local: dict[str, Any], requested: str | None
) -> None:
    # The org's enable_auto_approve = false excludes the yolo policy like its
    # lists do: the session starts another policy under the org config,
    # instead of failing and falling back to local config, where YOLO is on.
    orchestrator = await _org_orchestrator(
        local=local, policy="enable_auto_approve = false\n"
    )

    agents = AgentManager(orchestrator, initial_policy=requested)

    assert agents.active_policy.name == ApprovalPolicyName.ACCEPT_EDITS
    assert agents.excluded_agent == requested
    assert orchestrator.config.enable_auto_approve is False


@pytest.mark.asyncio
async def test_a_local_enabled_agents_does_not_widen_the_orgs() -> None:
    # The merged list joins both: auto-approve would pass a check on it.
    orchestrator = await _org_orchestrator(
        local={"enabled_agents": ["auto-approve"]}, policy='enabled_agents = ["ask"]\n'
    )

    agents = AgentManager(orchestrator, initial_policy="auto-approve")

    assert agents.active_policy.name == ApprovalPolicyName.ASK
    assert agents.excluded_agent == "auto-approve"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "requested", "rollout"),
    [
        # Asking for smart-approve opts in past the rollout gate, not past the
        # org's lists.
        ('disabled_agents = ["smart-approve"]\n', "smart-approve", None),
        # Nor does the rollout's default.
        ('disabled_agents = ["smart-approve"]\n', None, "smart-approve"),
        ('enabled_agents = ["ask", "accept-edits"]\n', None, "smart-approve"),
        # A fallback the org excludes is skipped too.
        (
            'disabled_agents = ["auto-approve", "smart-approve"]\n',
            "auto-approve",
            "smart-approve",
        ),
    ],
)
async def test_smart_approve_the_org_excludes_never_starts(
    policy: str, requested: str | None, rollout: str | None
) -> None:
    orchestrator = await _org_orchestrator(policy=policy, rollout=rollout)

    agents = AgentManager(orchestrator, initial_policy=requested)

    assert agents.active_policy.name == ApprovalPolicyName.ACCEPT_EDITS
    assert agents.excluded_agent == requested


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "selection", "error"),
    [
        # A typo is not the org's doing.
        (
            'default_approval_policy = "ask"\ndisabled_agents = ["auto-approve"]\n',
            {"initial_policy": "auto-aprove"},  # typos:disable-line
            "not found",
        ),
        # Nor is asking for a subagent as the primary agent.
        (
            'default_approval_policy = "ask"\ndisabled_agents = ["auto-approve"]\n',
            {"initial_agent": "explore"},
            "cannot be used as the primary agent",
        ),
        # Even when the org's list leaves the subagent out.
        pytest.param(
            'enabled_agents = ["ask", "accept-edits"]\n',
            {"initial_agent": "explore"},
            "explore",
        ),
    ],
)
async def test_a_failure_the_org_did_not_cause_stays_loud(
    policy: str, selection: dict[str, str], error: str
) -> None:
    orchestrator = await _org_orchestrator(policy=policy)

    with pytest.raises(ValueError, match=error):
        AgentManager(
            orchestrator,
            selection.get("initial_agent"),
            initial_policy=selection.get("initial_policy"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local", "error"),
    [
        (
            {"default_approval_policy": "acept-edits"},  # typos:disable-line
            "not found",
        ),
        pytest.param(
            {"default_agent": "acept-edits"},  # typos:disable-line
            "not found",
        ),
        pytest.param({"default_agent": "explore"}, "disabled by your organization"),
    ],
)
async def test_a_broken_saved_default_stays_loud_under_org_lists(
    local: dict[str, Any], error: str
) -> None:
    # The org's list leaves the name out, but the user's own config is what
    # needs fixing.
    orchestrator = await _org_orchestrator(
        local=local, policy='enabled_agents = ["ask", "accept-edits"]\n'
    )

    with pytest.raises(ValueError, match=error):
        AgentManager(orchestrator)


@pytest.mark.asyncio
async def test_the_orgs_lists_bound_every_policy_switch() -> None:
    # The user's own list widens the merged one; the picker, policy cycling and
    # a restored session policy still never reach what the org excludes.
    orchestrator = await _org_orchestrator(
        local={"enabled_agents": ["auto-approve", "accept-edits", "ask"]},
        policy='disabled_agents = ["auto-approve"]\n',
    )
    agents = AgentManager(orchestrator, initial_policy="accept-edits")

    assert "auto-approve" not in agents.available_policies
    assert "auto-approve" not in agents.get_policy_order()
    with pytest.raises(
        ValueError,
        match="Approval policy 'auto-approve' is disabled by your organization.",
    ):
        agents.switch_policy("auto-approve")
