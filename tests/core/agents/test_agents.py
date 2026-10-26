from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.agents.manager import AgentManager, resolve_default_agent
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    EXPLORE,
    AgentProfile,
    AgentSafety,
    AgentType,
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

    def test_get_subagents_excludes_agents(self, manager: AgentManager) -> None:
        """Test that AGENT type agents are not returned."""
        subagents = manager.get_subagents()
        names = [a.name for a in subagents]

        # These are AGENT type
        assert "ask" not in names
        assert "accept-edits" not in names
        assert "auto-approve" not in names

    def test_get_builtin_agent(self, manager: AgentManager) -> None:
        """Test getting a builtin agent by name."""
        agent = manager.get_agent("explore")

        assert agent is EXPLORE
        assert agent.agent_type == AgentType.SUBAGENT

    def test_get_nonexistent_agent_raises(self, manager: AgentManager) -> None:
        """Test that getting a nonexistent agent raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            manager.get_agent("nonexistent-agent")

    def test_get_ask_agent(self, manager: AgentManager) -> None:
        agent = manager.get_agent("ask")

        assert agent.name == "ask"
        assert agent.agent_type == AgentType.AGENT

    def test_smart_approve_hidden_from_the_picker_by_default(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Smart approve ships dark: without the flag it is not in the picker/cycle."""
        manager = AgentManager(load_orchestrator(build_config()))

        assert "smart-approve" not in manager.available_agents
        assert "smart-approve" not in manager.get_agent_order()

    def test_smart_approve_available_flag_exposes_it_in_the_picker(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """The availability flag adds smart approve to the picker/cycle."""
        config = build_config(smart_approve_available=True)
        manager = AgentManager(load_orchestrator(config))

        assert "smart-approve" in manager.available_agents
        assert "smart-approve" in manager.get_agent_order()

    def test_smart_approve_default_flag_offers_and_defaults(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """The default flag both exposes smart approve and makes it the resolved default."""
        config = build_config(smart_approve_default=True)
        orchestrator = load_orchestrator(config)
        manager = AgentManager(
            orchestrator, initial_agent=resolve_default_agent(orchestrator)
        )

        assert "smart-approve" in manager.available_agents
        assert manager.active_profile.name == "smart-approve"

    def test_explicit_smart_approve_selection_bypasses_the_gate(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Explicitly selecting smart approve works even when the picker gate is off."""
        manager = AgentManager(
            load_orchestrator(build_config()), initial_agent="smart-approve"
        )

        assert manager.active_profile.name == "smart-approve"

    def test_explicit_smart_approve_stays_in_the_cycle(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """A --smart-approve start keeps the mode in the cycle after switching away.

        Regression: the mode was active at startup but dropped out of the picker
        once the user cycled off it, because it was hidden by the rollout gate.
        """
        manager = AgentManager(
            load_orchestrator(build_config()), initial_agent="smart-approve"
        )

        assert "smart-approve" in manager.available_agents
        assert "smart-approve" in manager.get_agent_order()

        # Cycle a full loop; smart-approve must be reachable again, not skipped.
        order = manager.get_agent_order()
        current = manager.active_profile
        seen = [current.name]
        for _ in range(len(order)):
            current = manager.next_agent(current)
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
        assert manager.active_profile.name == "explore"

    def test_initial_agent_accepts_agent_type(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Test that creating AgentManager with an agent-type agent works."""
        config = build_config()
        manager = AgentManager(load_orchestrator(config), initial_agent="ask")
        assert manager.active_profile.name == "ask"

    def test_initial_agent_raises_when_agent_is_disabled(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(disabled_agents=["ask"])
        with pytest.raises(ValueError, match="disabled_agents") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="ask")
        message = str(exc_info.value)
        assert "default_agent" not in message
        assert message.startswith("Agent 'ask'")

    def test_explicit_agent_excluded_by_enabled_agents_does_not_blame_default(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["ask"])
        with pytest.raises(ValueError, match="enabled_agents") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="auto-approve")
        message = str(exc_info.value)
        assert "default_agent" not in message
        assert message.startswith("Agent 'auto-approve'")

    def test_initial_agent_raises_when_agent_does_not_exist(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config()
        with pytest.raises(ValueError, match="not found"):
            AgentManager(load_orchestrator(config), initial_agent="nonexistent-agent")

    def test_default_agent_excluded_by_enabled_agents_raises_config_contradiction(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["ask"])
        with pytest.raises(ValueError, match="enabled_agents") as exc_info:
            AgentManager(load_orchestrator(config))
        message = str(exc_info.value)
        assert "accept-edits" in message
        assert "default_agent" in message

    def test_default_agent_excluded_by_disabled_agents_raises_config_contradiction(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(disabled_agents=["accept-edits"])
        with pytest.raises(ValueError, match="disabled_agents") as exc_info:
            AgentManager(load_orchestrator(config))
        assert "default_agent" in str(exc_info.value)

    def test_disabled_agents_ignored_entirely_when_enabled_agents_set(
        self,
        caplog: pytest.LogCaptureFixture,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["ask"], disabled_agents=["ask"])
        with caplog.at_level("WARNING"):
            manager = AgentManager(load_orchestrator(config), initial_agent="ask")
        assert manager.active_profile.name == "ask"
        assert caplog.text == ""

    def test_auto_approve_agent_available_by_default(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        manager = AgentManager(load_orchestrator(build_config()))

        assert "auto-approve" in manager.available_agents
        assert "auto-approve" in manager.get_agent_order()

    def test_disabling_auto_approve_mode_hides_the_agent(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """enable_auto_approve = false removes the yolo agent from the picker/cycle."""
        config = build_config(enable_auto_approve=False)
        manager = AgentManager(load_orchestrator(config))

        assert "auto-approve" not in manager.available_agents
        assert "auto-approve" not in manager.get_agent_order()

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

        assert "auto-approve" not in manager.available_agents
        assert "ask" in manager.available_agents

    def test_initial_agent_auto_approve_rejected_when_mode_disabled(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enable_auto_approve=False)
        with pytest.raises(
            ValueError, match="auto-approve mode is disabled"
        ) as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="auto-approve")

        assert "admin-managed" not in str(exc_info.value)

    def test_switching_to_auto_approve_rejected_when_mode_disabled(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enable_auto_approve=False)
        manager = AgentManager(load_orchestrator(config), initial_agent="ask")

        with pytest.raises(ValueError, match="auto-approve mode is disabled"):
            manager.switch_profile("auto-approve")

    def test_custom_yolo_agent_hidden_when_mode_disabled(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """The mode gate keys on the safety level, so custom yolo agents are covered."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "reckless.toml").write_text('safety = "yolo"\n', encoding="utf-8")

        enabled_manager = AgentManager(
            load_orchestrator(build_config(agent_paths=[agents_dir]))
        )
        assert "reckless" in enabled_manager.available_agents

        config = build_config(enable_auto_approve=False, agent_paths=[agents_dir])
        manager = AgentManager(load_orchestrator(config))
        assert "reckless" not in manager.available_agents

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

        manager = AgentManager(load_orchestrator(config), initial_agent="ask")
        assert "slow" in manager.available_agents
        with pytest.raises(InvalidConfigError) as switched:
            manager.switch_profile("slow")
        assert str(switched.value) == expected
        assert manager.active_profile.name == "ask"
        assert "default_timeout" not in manager.config.tools.get("bash", {})

    def test_a_broken_override_of_a_builtin_agent_keeps_the_builtin(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """*Prepare*: agents/accept-edits.toml sets only one of the two bash
        timeouts.
        *Do*: Start in accept-edits, the default mode.
        *Assert*: The session starts on the builtin, and the ignored file is
        logged with its config error.
        """
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        path = agents_dir / "accept-edits.toml"
        path.write_text("[tools.bash]\ndefault_timeout = 30\n", encoding="utf-8")
        config = build_config(agent_paths=[agents_dir])

        with caplog.at_level("WARNING"):
            manager = AgentManager(
                load_orchestrator(config), initial_agent=BuiltinAgentName.ACCEPT_EDITS
            )

        assert manager.active_profile is BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS]
        assert any(
            str(path) in record.getMessage()
            and "default_timeout is set without max_timeout_seconds"
            in record.getMessage()
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
            manager = AgentManager(
                orchestrator, initial_agent=resolve_default_agent(orchestrator)
            )

        assert manager.active_profile.name == "ask"
        assert "plan" not in manager.available_agents
        assert "default_agent 'plan' is no longer a builtin agent" in caplog.text

    def test_explicit_plan_agent_is_rejected_like_any_unknown_agent(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        with pytest.raises(ValueError, match="Agent 'plan' not found"):
            AgentManager(load_orchestrator(build_config()), initial_agent="plan")

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
        manager = AgentManager(
            orchestrator, initial_agent=resolve_default_agent(orchestrator)
        )

        assert manager.active_profile.name == "plan"
        assert manager.active_profile.display_name == "My Plan"

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
        assert manager.active_profile.name == "reviewer"

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

        assert manager.active_profile.name == "legacy"

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
    *, local: dict[str, Any] | None = None, policy: str = "", cohort: bool = False
) -> ConfigOrchestrator[VibeConfigSchema]:
    growthbook = GrowthbookLayer()
    growthbook.set_variants({"vibe_cli_smart_approve_default": cohort})
    admin = AdminConfigLayer()
    admin.load_managed_toml(policy)
    return await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[growthbook, OverridesLayer(data=local or {}), admin],
        default_layer_resolver=lambda: admin,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local", "policy", "cohort", "expected"),
    [
        ({}, "", False, BuiltinAgentName.ACCEPT_EDITS),
        ({"default_agent": "ask"}, "", False, BuiltinAgentName.ASK),
        # The smart-approve experiment's cohort default beats the local one.
        ({"default_agent": "ask"}, "", True, BuiltinAgentName.SMART_APPROVE),
        # An org-enforced default beats the experiment.
        ({}, 'default_agent = "ask"\n', True, BuiltinAgentName.ASK),
        # Even when the org also enforces the experiment's flag: an org that
        # wants smart-approve as the default enforces it as default_agent.
        (
            {},
            'default_agent = "ask"\nsmart_approve_default = true\n',
            True,
            BuiltinAgentName.ASK,
        ),
        # An org config without default_agent leaves the experiment alone.
        ({}, 'theme = "nord"\n', True, BuiltinAgentName.SMART_APPROVE),
    ],
)
async def test_resolve_default_agent(
    local: dict[str, Any], policy: str, cohort: bool, expected: str
) -> None:
    orchestrator = await _org_orchestrator(local=local, policy=policy, cohort=cohort)

    assert resolve_default_agent(orchestrator) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "requested", "started"),
    [
        # The user's own default, excluded by the org: a builtin mode.
        ('disabled_agents = ["auto-approve"]\n', None, "accept-edits"),
        # An --agent choice the org excludes: a builtin mode.
        ('disabled_agents = ["auto-approve"]\n', "auto-approve", "accept-edits"),
        # ... or the org's own default, when it enforces one.
        (
            'default_agent = "ask"\ndisabled_agents = ["auto-approve"]\n',
            "auto-approve",
            "ask",
        ),
        (
            'default_agent = "ask"\nenabled_agents = ["ask", "accept-edits"]\n',
            "auto-approve",
            "ask",
        ),
    ],
)
async def test_an_agent_the_org_excludes_starts_another_mode(
    policy: str, requested: str | None, started: str
) -> None:
    orchestrator = await _org_orchestrator(
        local={"default_agent": "auto-approve"}, policy=policy
    )

    agents = AgentManager(orchestrator, requested)

    assert agents.active_profile.name == started
    assert agents.excluded_agent == requested


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [None, "auto-approve"])
async def test_an_enforced_plan_default_runs_as_ask_under_agent_lists(
    requested: str | None,
) -> None:
    # "plan" starts as ask, which the org's list allows, whether it is the
    # starting mode or the fallback for an excluded --agent choice.
    orchestrator = await _org_orchestrator(
        policy='default_agent = "plan"\nenabled_agents = ["ask", "accept-edits"]\n'
    )

    agents = AgentManager(orchestrator, requested)

    assert agents.active_profile.name == BuiltinAgentName.ASK


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy", ['disabled_agents = ["ask"]\n', 'enabled_agents = ["accept-edits"]\n']
)
async def test_a_saved_plan_default_the_org_excludes_as_ask_starts_another_mode(
    policy: str,
) -> None:
    # A pre-3.0 saved "plan" starts as ask: the org's lists are checked on ask.
    orchestrator = await _org_orchestrator(
        local={"default_agent": "plan"}, policy=policy
    )

    agents = AgentManager(orchestrator, None)

    assert agents.active_profile.name == BuiltinAgentName.ACCEPT_EDITS
    assert agents.excluded_agent is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local", "requested"),
    [({"default_agent": "auto-approve"}, None), ({}, "auto-approve")],
)
async def test_an_org_that_forbids_auto_approve_starts_another_mode(
    local: dict[str, Any], requested: str | None
) -> None:
    # The org's enable_auto_approve = false excludes the yolo mode like its
    # agent lists do: the session starts another mode under the org config,
    # instead of failing and falling back to local config, where YOLO is on.
    orchestrator = await _org_orchestrator(
        local=local, policy="enable_auto_approve = false\n"
    )

    agents = AgentManager(orchestrator, requested)

    assert agents.active_profile.name == BuiltinAgentName.ACCEPT_EDITS
    assert agents.excluded_agent == requested
    assert orchestrator.config.enable_auto_approve is False


@pytest.mark.asyncio
async def test_an_org_that_forbids_auto_approve_excludes_custom_yolo_agents(
    tmp_path: Path,
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "reckless.toml").write_text('safety = "yolo"\n', encoding="utf-8")
    orchestrator = await _org_orchestrator(
        local={"agent_paths": [str(agents_dir)]}, policy="enable_auto_approve = false\n"
    )

    agents = AgentManager(orchestrator, "reckless")

    assert agents.active_profile.name == BuiltinAgentName.ACCEPT_EDITS
    assert agents.excluded_agent == "reckless"
    with pytest.raises(
        ValueError, match="Agent 'reckless' is disabled by your organization."
    ):
        agents.switch_profile("reckless")


@pytest.mark.asyncio
async def test_a_local_enabled_agents_does_not_widen_the_orgs() -> None:
    # The merged list joins both: auto-approve would pass a check on it.
    orchestrator = await _org_orchestrator(
        local={"enabled_agents": ["auto-approve"]}, policy='enabled_agents = ["ask"]\n'
    )

    agents = AgentManager(orchestrator, "auto-approve")

    assert agents.active_profile.name == BuiltinAgentName.ASK
    assert agents.excluded_agent == "auto-approve"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "requested", "cohort"),
    [
        # Asking for smart-approve opts in past the rollout gate, not past the
        # org's agent lists.
        ('disabled_agents = ["smart-approve"]\n', "smart-approve", False),
        # Nor does the experiment's cohort default.
        ('disabled_agents = ["smart-approve"]\n', None, True),
        ('enabled_agents = ["ask", "accept-edits"]\n', None, True),
        # A fallback the org excludes is skipped too.
        ('disabled_agents = ["auto-approve", "smart-approve"]\n', "auto-approve", True),
    ],
)
async def test_smart_approve_the_org_excludes_never_starts(
    policy: str, requested: str | None, cohort: bool
) -> None:
    orchestrator = await _org_orchestrator(policy=policy, cohort=cohort)

    agents = AgentManager(orchestrator, requested)

    assert agents.active_profile.name == BuiltinAgentName.ACCEPT_EDITS
    assert agents.excluded_agent == requested


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "requested", "error"),
    [
        # A typo is not the org's doing.
        (
            'default_agent = "ask"\ndisabled_agents = ["auto-approve"]\n',
            "auto-aprove",  # typos:disable-line
            "not found",
        ),
        # Nor is asking for a subagent as the primary agent.
        (
            'default_agent = "ask"\ndisabled_agents = ["auto-approve"]\n',
            "explore",
            "cannot be used as the primary agent",
        ),
        # Even when the org's mode list leaves the subagent out.
        (
            'enabled_agents = ["ask", "accept-edits"]\n',
            "explore",
            "'explore' is disabled by your organization",
        ),
    ],
)
async def test_a_failure_the_org_did_not_cause_stays_loud(
    policy: str, requested: str, error: str
) -> None:
    orchestrator = await _org_orchestrator(policy=policy)

    with pytest.raises(ValueError, match=error):
        AgentManager(orchestrator, requested)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("default_agent", "error"),
    [
        ("acept-edits", "not found"),  # typos:disable-line
        ("explore", "disabled by your organization"),
    ],
)
async def test_a_broken_saved_default_stays_loud_under_org_agent_lists(
    default_agent: str, error: str
) -> None:
    # The org's list leaves the name out, but the user's own config is what
    # needs fixing.
    orchestrator = await _org_orchestrator(
        local={"default_agent": default_agent},
        policy='enabled_agents = ["ask", "accept-edits"]\n',
    )

    with pytest.raises(ValueError, match=error):
        AgentManager(orchestrator, None)


@pytest.mark.asyncio
async def test_the_orgs_agent_lists_bound_every_mode_switch() -> None:
    # The user's own list widens the merged one; the picker, mode cycling and
    # a restored session mode still never reach what the org excludes.
    orchestrator = await _org_orchestrator(
        local={"enabled_agents": ["auto-approve", "accept-edits", "ask"]},
        policy='disabled_agents = ["auto-approve"]\n',
    )
    agents = AgentManager(orchestrator, "accept-edits")

    assert "auto-approve" not in agents.available_agents
    with pytest.raises(
        ValueError, match="Agent 'auto-approve' is disabled by your organization."
    ):
        agents.switch_profile("auto-approve")
