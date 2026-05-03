from __future__ import annotations

from unittest.mock import Mock

from vibe.cli.textual_ui.widgets.banner.banner import Banner, BannerState, _pluralize
from vibe.core.config import VibeConfig
from vibe.core.config._settings import ModelConfig, ThinkingLevel
from vibe.core.skills.manager import SkillManager
from vibe.core.tools.mcp.registry import MCPRegistry


def _make_mock_config(
    active_model: str = "test-model", thinking: ThinkingLevel = "off"
) -> Mock:
    config = Mock(spec=VibeConfig)
    config.active_model = active_model
    config.models = [active_model]
    config.mcp_servers = []
    config.connectors = []
    config.disable_welcome_banner_animation = False
    config.get_active_model.return_value = ModelConfig(
        name=active_model, provider="mistral", alias=active_model, thinking=thinking
    )
    return config


class TestBannerInitialState:
    """Test that Banner properly displays initial state including connectors/MCP."""

    def test_pluralize(self) -> None:
        """Test pluralization helper."""
        assert _pluralize(0, "model") == "0 models"
        assert _pluralize(1, "model") == "1 model"
        assert _pluralize(2, "model") == "2 models"
        assert _pluralize(0, "MCP server") == "0 MCP servers"
        assert _pluralize(1, "MCP server") == "1 MCP server"
        assert _pluralize(2, "connector") == "2 connectors"

    def test_banner_initial_state_includes_connectors(self) -> None:
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        mcp_registry = Mock(spec=MCPRegistry)
        mcp_registry.count_loaded.return_value = 0

        banner = Banner(
            config=_make_mock_config(),
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
            connectors_count=5,
        )

        assert banner._initial_state.active_model == "test-model[off]"
        assert banner._initial_state.models_count == 1
        assert banner._initial_state.mcp_servers_count == 0
        assert banner._initial_state.connectors_count == 5
        assert banner._initial_state.skills_count == 0

    def test_banner_initial_state_with_no_connectors(self) -> None:
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        mcp_registry = Mock(spec=MCPRegistry)
        mcp_registry.count_loaded.return_value = 0

        banner = Banner(
            config=_make_mock_config(),
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
        )

        assert banner._initial_state.connectors_count == 0

    def test_banner_shows_thinking_level(self) -> None:
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        mcp_registry = Mock(spec=MCPRegistry)
        mcp_registry.count_loaded.return_value = 0

        banner = Banner(
            config=_make_mock_config(thinking="max"),
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
        )

        assert banner._initial_state.active_model == "test-model[max]"

    def test_format_meta_counts_includes_connectors(self) -> None:
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        mcp_registry = Mock(spec=MCPRegistry)
        mcp_registry.count_loaded.return_value = 0

        banner = Banner(
            config=_make_mock_config(),
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
        )

        # Now test _format_meta_counts by setting state
        banner.state = BannerState(
            models_count=2, mcp_servers_count=1, connectors_count=3, skills_count=5
        )
        result = banner._format_meta_counts()
        assert "2 models" in result
        assert "3 connectors" in result
        assert "1 MCP server" in result
        assert "5 skills" in result

        # Test without connectors
        banner.state = BannerState(
            models_count=2, mcp_servers_count=1, connectors_count=0, skills_count=5
        )
        result = banner._format_meta_counts()
        assert "2 models" in result
        assert "connectors" not in result  # Should not appear when 0
        assert "1 MCP server" in result
        assert "5 skills" in result


class TestBannerConnectorsCount:
    def test_connectors_count_passed_through(self) -> None:
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        mcp_registry = Mock(spec=MCPRegistry)
        mcp_registry.count_loaded.return_value = 0

        banner = Banner(
            config=_make_mock_config(),
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
            connectors_count=5,
        )

        assert banner._initial_state.connectors_count == 5
