"""Integration tests for mode system with Agent."""

from __future__ import annotations

import pytest

from vibe.core.agent import Agent
from vibe.core.config import VibeConfig


class TestAgentModeIntegration:
    """Test Agent integration with mode system."""

    @pytest.fixture
    def config(self):
        """Create a basic config for testing."""
        return VibeConfig()

    def test_agent_default_mode(self, config):
        """Test agent starts in normal mode by default."""
        agent = Agent(config=config)
        assert agent.get_current_mode().id == "normal"
        assert agent.auto_approve is False

    def test_agent_with_auto_approve_flag(self, config):
        """Test agent with auto_approve=True maps to auto-approve mode."""
        agent = Agent(config=config, auto_approve=True)
        assert agent.get_current_mode().id == "auto-approve"
        assert agent.auto_approve is True

    def test_agent_with_explicit_mode(self, config):
        """Test agent with explicit initial_mode parameter."""
        agent = Agent(config=config, initial_mode="accept-edits")
        assert agent.get_current_mode().id == "accept-edits"
        assert agent.auto_approve is False

    def test_agent_set_mode(self, config):
        """Test changing agent mode."""
        agent = Agent(config=config)
        assert agent.get_current_mode().id == "normal"

        # Change to auto-approve
        agent.set_mode("auto-approve")
        assert agent.get_current_mode().id == "auto-approve"
        assert agent.auto_approve is True

        # Change to plan
        agent.set_mode("plan")
        assert agent.get_current_mode().id == "plan"
        assert agent.auto_approve is False

    def test_agent_set_invalid_mode_raises(self, config):
        """Test setting invalid mode raises ValueError."""
        agent = Agent(config=config)
        with pytest.raises(ValueError, match="Unknown mode"):
            agent.set_mode("nonexistent-mode")

    def test_agent_list_modes(self, config):
        """Test agent can list available modes."""
        agent = Agent(config=config)
        modes = agent.list_modes()
        mode_ids = [m.id for m in modes]
        assert "normal" in mode_ids
        assert "auto-approve" in mode_ids
        assert "accept-edits" in mode_ids
        assert "plan" in mode_ids


class TestAgentWithCustomModes:
    """Test Agent with custom mode configurations."""

    @pytest.fixture
    def config_with_custom_modes(self):
        """Create config with custom modes."""
        config = VibeConfig(
            modes={
                "readonly": {
                    "name": "Read Only",
                    "description": "Read-only mode",
                    "tool_permissions": {
                        "grep": "always",
                        "read_file": "always",
                        "*": "never",
                    },
                }
            }
        )
        return config

    def test_agent_with_custom_mode_in_config(self, config_with_custom_modes):
        """Test agent initializes with custom modes from config."""
        agent = Agent(config=config_with_custom_modes)
        modes = agent.list_modes()
        mode_ids = [m.id for m in modes]
        assert "readonly" in mode_ids

    def test_agent_can_use_custom_mode(self, config_with_custom_modes):
        """Test agent can switch to custom mode."""
        agent = Agent(config=config_with_custom_modes, initial_mode="readonly")
        assert agent.get_current_mode().id == "readonly"
        assert agent.get_current_mode().name == "Read Only"
        assert agent.get_current_mode().is_custom is True

    def test_custom_mode_in_initial_mode_config(self):
        """Test using custom mode as initial_mode in config."""
        config = VibeConfig(
            initial_mode="my-mode",
            modes={
                "my-mode": {"name": "My Mode", "description": "Custom initial mode"}
            },
        )
        agent = Agent(config=config)
        assert agent.get_current_mode().id == "my-mode"


class TestBackwardCompatibility:
    """Test backward compatibility with old auto_approve behavior."""

    @pytest.fixture
    def config(self):
        """Create a basic config for testing."""
        return VibeConfig()

    def test_auto_approve_property_reflects_mode(self, config):
        """Test that auto_approve property reflects current mode."""
        agent = Agent(config=config)

        # Normal mode -> auto_approve False
        assert agent.get_current_mode().id == "normal"
        assert agent.auto_approve is False

        # Switch to auto-approve mode -> auto_approve True
        agent.set_mode("auto-approve")
        assert agent.auto_approve is True

        # Switch to accept-edits -> auto_approve False
        agent.set_mode("accept-edits")
        assert agent.auto_approve is False

    def test_auto_approve_flag_constructor(self, config):
        """Test old auto_approve constructor parameter still works."""
        # Old style: Agent(config, auto_approve=True)
        agent = Agent(config=config, auto_approve=True)
        assert agent.get_current_mode().id == "auto-approve"
        assert agent.auto_approve is True

        # Old style: Agent(config, auto_approve=False)
        agent2 = Agent(config=config, auto_approve=False)
        assert agent2.get_current_mode().id == "normal"
        assert agent2.auto_approve is False

    def test_initial_mode_overrides_auto_approve(self, config):
        """Test that explicit initial_mode overrides auto_approve flag."""
        # If both provided, initial_mode takes precedence
        agent = Agent(config=config, auto_approve=True, initial_mode="plan")
        assert agent.get_current_mode().id == "plan"
        assert agent.auto_approve is False


class TestModeRegistryBuilding:
    """Test mode registry is built correctly during Agent initialization."""

    def test_registry_built_on_agent_init(self):
        """Test agent builds its own mode registry correctly."""
        config = VibeConfig(
            modes={"test-mode": {"name": "Test", "description": "Test mode"}}
        )

        # Registry should be built when agent is created
        agent = Agent(config=config)

        # Check agent has all expected modes
        mode_ids = [m.id for m in agent.list_modes()]
        assert "normal" in mode_ids
        assert "auto-approve" in mode_ids
        assert "test-mode" in mode_ids

    def test_registry_not_polluted_between_tests(self):
        """Test that custom modes from one agent don't affect others."""
        # First agent with custom mode
        config1 = VibeConfig(modes={"mode1": {"name": "Mode 1", "description": "Test"}})
        agent1 = Agent(config=config1)

        # Second agent with different custom mode
        config2 = VibeConfig(modes={"mode2": {"name": "Mode 2", "description": "Test"}})
        agent2 = Agent(config=config2)

        # Each agent should have its own modes
        agent1_modes = [m.id for m in agent1.list_modes()]
        agent2_modes = [m.id for m in agent2.list_modes()]

        # Both should have predefined modes
        assert "normal" in agent1_modes
        assert "normal" in agent2_modes

        # Each agent should ONLY have its own custom mode (no pollution!)
        assert "mode1" in agent1_modes
        assert "mode1" not in agent2_modes  # Should not pollute agent2
        assert "mode2" in agent2_modes
        assert "mode2" not in agent1_modes  # Should not pollute agent1


class TestConfigValidation:
    """Test config validation for modes."""

    def test_config_rejects_reserved_mode_id(self):
        """Test config rejects attempts to override predefined modes."""
        with pytest.raises(ValueError, match="is reserved"):
            VibeConfig(
                modes={
                    "normal": {  # Try to override
                        "name": "Custom Normal",
                        "description": "Override",
                    }
                }
            )

    def test_config_validates_mode_id_format(self):
        """Test config validates mode ID format."""
        with pytest.raises(ValueError, match="Invalid mode ID"):
            VibeConfig(
                modes={
                    "Invalid_Mode": {  # Invalid format (underscore)
                        "name": "Invalid",
                        "description": "Test",
                    }
                }
            )

    def test_config_accepts_valid_custom_mode(self):
        """Test config accepts valid custom modes."""
        config = VibeConfig(
            modes={
                "my-custom-mode": {
                    "name": "My Custom Mode",
                    "description": "A valid custom mode",
                    "tool_permissions": {"grep": "always"},
                }
            }
        )
        assert "my-custom-mode" in config.modes

    def test_invalid_initial_mode_falls_back(self):
        """Test that invalid initial_mode falls back to normal."""
        config = VibeConfig(initial_mode="nonexistent")
        agent = Agent(config=config)
        # Should fall back to normal mode
        assert agent.get_current_mode().id == "normal"
