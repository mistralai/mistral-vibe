from __future__ import annotations

from unittest.mock import Mock

from vibe.cli.textual_ui.widgets.banner.banner import Banner, BannerState, _pluralize
from vibe.core.config import VibeConfig
from vibe.core.config._settings import ModelConfig, ThinkingLevel
from vibe.core.skills.manager import SkillManager


def _make_mock_config(
    active_model: str = "test-model",
    thinking: ThinkingLevel = "off",
    mcp_servers: list | None = None,
) -> Mock:
    config = Mock(spec=VibeConfig)
    config.active_model = active_model
    config.models = [active_model]
    config.mcp_servers = mcp_servers or []
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

        banner = Banner(
            config=_make_mock_config(),
            skill_manager=skill_manager,
            connectors_connected=5,
            connectors_total=5,
        )

        assert banner._initial_state.active_model == "test-model[off]"
        assert banner._initial_state.models_count == 1
        assert (
            banner._initial_state.mcp_servers_enabled == 0
        )  # No MCP servers configured
        assert banner._initial_state.mcp_servers_total == 0
        assert banner._initial_state.connectors_connected == 5
        assert banner._initial_state.connectors_total == 5
        assert banner._initial_state.skills_count == 0

    def test_banner_initial_state_with_no_connectors(self) -> None:
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        banner = Banner(config=_make_mock_config(), skill_manager=skill_manager)

        assert banner._initial_state.connectors_connected == 0
        assert banner._initial_state.connectors_total == 0

    def test_banner_shows_thinking_level(self) -> None:
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        banner = Banner(
            config=_make_mock_config(thinking="max"), skill_manager=skill_manager
        )

        assert banner._initial_state.active_model == "test-model[max]"

    def test_format_meta_counts_includes_connectors(self) -> None:
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        banner = Banner(config=_make_mock_config(), skill_manager=skill_manager)

        # Now test _format_meta_counts by setting state with x/y format
        banner.state = BannerState(
            models_count=2,
            mcp_servers_enabled=1,
            mcp_servers_total=2,
            connectors_connected=3,
            connectors_total=3,
            skills_count=5,
        )
        result = banner._format_meta_counts()
        assert "2 models" in result
        assert "3 connectors" in result  # When enabled == total, just show count
        assert "1/2 MCP servers" in result
        assert "5 skills" in result

        # Test without connectors
        banner.state = BannerState(
            models_count=2,
            mcp_servers_enabled=1,
            mcp_servers_total=2,
            connectors_connected=0,
            connectors_total=0,
            skills_count=5,
        )
        result = banner._format_meta_counts()
        assert "2 models" in result
        assert "connectors" not in result  # Should not appear when 0
        assert "1/2 MCP servers" in result
        assert "5 skills" in result


class TestBannerMCPServersCount:
    """Test that banner correctly counts MCP servers regardless of tool discovery."""

    def test_banner_counts_enabled_mcp_servers(self) -> None:
        """Test that banner counts all enabled MCP servers, not just those with tools."""
        from vibe.core.config import MCPServer

        # Create mock MCP servers - even if they have no tools, they should be counted
        mock_server1 = Mock(spec=MCPServer)
        mock_server1.name = "server1"
        mock_server1.disabled = False

        mock_server2 = Mock(spec=MCPServer)
        mock_server2.name = "server2"
        mock_server2.disabled = False

        # Create a disabled server that should NOT be counted
        mock_server3 = Mock(spec=MCPServer)
        mock_server3.name = "server3"
        mock_server3.disabled = True

        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        # Note: we don't need to mock count_loaded anymore since it's not used

        banner = Banner(
            config=_make_mock_config(
                mcp_servers=[mock_server1, mock_server2, mock_server3]
            ),
            skill_manager=skill_manager,
        )

        # Should count only enabled servers (server1 and server2, not server3)
        assert banner._initial_state.mcp_servers_enabled == 2
        assert banner._initial_state.mcp_servers_total == 3

    def test_banner_shows_zero_mcp_servers(self) -> None:
        """Test that banner correctly shows 0 when no MCP servers are configured."""
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        banner = Banner(
            config=_make_mock_config(mcp_servers=[]), skill_manager=skill_manager
        )

        assert banner._initial_state.mcp_servers_enabled == 0
        assert banner._initial_state.mcp_servers_total == 0

    def test_banner_shows_disabled_count_in_xy_format(self) -> None:
        """Test that banner shows x/y format with disabled servers."""
        from vibe.core.config import MCPServer

        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        banner = Banner(
            config=_make_mock_config(
                mcp_servers=[
                    Mock(spec=MCPServer, name="s1", disabled=False),
                    Mock(spec=MCPServer, name="s2", disabled=False),
                    Mock(spec=MCPServer, name="s3", disabled=True),
                ]
            ),
            skill_manager=skill_manager,
        )

        assert banner._initial_state.mcp_servers_enabled == 2
        assert banner._initial_state.mcp_servers_total == 3
        # Test the formatted output using the initial state
        banner.state = banner._initial_state
        result = banner._format_meta_counts()
        assert "2/3 MCP servers" in result

    def test_banner_shows_simple_count_when_all_enabled(self) -> None:
        """Test that banner shows simple count when all MCP servers are enabled."""
        from vibe.core.config import MCPServer

        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        banner = Banner(
            config=_make_mock_config(
                mcp_servers=[
                    Mock(spec=MCPServer, name="s1", disabled=False),
                    Mock(spec=MCPServer, name="s2", disabled=False),
                ]
            ),
            skill_manager=skill_manager,
        )

        assert banner._initial_state.mcp_servers_enabled == 2
        assert banner._initial_state.mcp_servers_total == 2
        banner.state = banner._initial_state
        result = banner._format_meta_counts()
        # When all are enabled, show simple count not x/y
        assert "2 MCP servers" in result
        assert "/" not in result  # No slash when all enabled


class TestBannerConnectorsCount:
    def test_connectors_count_passed_through(self) -> None:
        skill_manager = Mock(spec=SkillManager)
        skill_manager.custom_skills_count = 0

        banner = Banner(
            config=_make_mock_config(),
            skill_manager=skill_manager,
            connectors_connected=3,
            connectors_total=5,
        )

        assert banner._initial_state.connectors_connected == 3
        assert banner._initial_state.connectors_total == 5
