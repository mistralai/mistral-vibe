"""Tests for the Banner widget initial state with connectors/MCP."""

from __future__ import annotations

from unittest.mock import Mock, patch

from vibe.cli.textual_ui.widgets.banner.banner import (
    Banner,
    BannerState,
    _connector_count,
    _pluralize,
)
from vibe.core.config import VibeConfig
from vibe.core.skills.manager import SkillManager
from vibe.core.tools.connectors.connector_registry import ConnectorRegistry
from vibe.core.tools.mcp.registry import MCPRegistry


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

    def test_connector_count_with_registry(self) -> None:
        """Test _connector_count with a populated registry."""
        registry = Mock(spec=ConnectorRegistry)
        registry.connector_count = 3
        assert _connector_count(registry) == 3

    def test_connector_count_without_registry(self) -> None:
        """Test _connector_count with None registry."""
        assert _connector_count(None) == 0

    def test_banner_initial_state_includes_connectors(self) -> None:
        """Test that Banner._initial_state includes connector count."""
        config = Mock(spec=VibeConfig)
        config.active_model = "test-model"
        config.models = ["test-model"]
        config.mcp_servers = []
        config.disable_welcome_banner_animation = False

        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        mcp_registry = Mock(spec=MCPRegistry)
        mcp_registry.count_loaded.return_value = 0

        connector_registry = Mock(spec=ConnectorRegistry)
        connector_registry.connector_count = 5

        banner = Banner(
            config=config,
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
            connector_registry=connector_registry,
        )

        assert banner._initial_state.active_model == "test-model"
        assert banner._initial_state.models_count == 1
        assert banner._initial_state.mcp_servers_count == 0
        assert banner._initial_state.connectors_count == 5
        assert banner._initial_state.skills_count == 0

    def test_banner_initial_state_with_none_connector_registry(self) -> None:
        """Test that Banner._initial_state handles None connector registry."""
        config = Mock(spec=VibeConfig)
        config.active_model = "test-model"
        config.models = ["test-model"]
        config.mcp_servers = []
        config.disable_welcome_banner_animation = False

        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        mcp_registry = Mock(spec=MCPRegistry)
        mcp_registry.count_loaded.return_value = 0

        banner = Banner(
            config=config,
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
            connector_registry=None,
        )

        assert banner._initial_state.connectors_count == 0

    def test_format_meta_counts_includes_connectors(self) -> None:
        """Test that _format_meta_counts includes connector count when > 0."""
        # Test _format_meta_counts by directly calling it on a Banner instance
        config = Mock(spec=VibeConfig)
        config.active_model = "test-model"
        config.models = []  # Must be a list for len() to work
        config.mcp_servers = []
        config.disable_welcome_banner_animation = False

        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        mcp_registry = Mock(spec=MCPRegistry)
        mcp_registry.count_loaded.return_value = 0

        banner = Banner(
            config=config,
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
            connector_registry=None,
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


class TestBannerWithEnabledConnectors:
    """Integration tests for Banner with EXPERIMENTAL_ENABLE_CONNECTORS=1."""

    @patch("vibe.core.tools.connectors.connectors_enabled")
    def test_connectors_enabled_flag(self, mock_enabled: Mock) -> None:
        """Test that connector count is read when enabled."""
        mock_enabled.return_value = True

        config = Mock(spec=VibeConfig)
        config.active_model = "test-model"
        config.models = ["test-model"]
        config.mcp_servers = []
        config.disable_welcome_banner_animation = False

        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        mcp_registry = Mock(spec=MCPRegistry)
        mcp_registry.count_loaded.return_value = 0

        connector_registry = Mock(spec=ConnectorRegistry)
        connector_registry.connector_count = 5

        banner = Banner(
            config=config,
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
            connector_registry=connector_registry,
        )

        assert banner._initial_state.connectors_count == 5
