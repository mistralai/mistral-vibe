"""Tests for the mode system (vibe/core/modes.py)."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from vibe.core.modes import (
    PREDEFINED_MODES,
    ModeConfig,
    PathRestrictionConfig,
    build_mode_registry,
    get_mode_config,
    list_available_modes,
)
from vibe.core.tools.base import ToolPermission


class TestModeConfig:
    """Test ModeConfig validation and behavior."""

    def test_valid_mode_config(self):
        """Test creating a valid mode config."""
        mode = ModeConfig(
            id="test-mode",
            name="Test Mode",
            description="A test mode",
            tool_permissions={"grep": ToolPermission.ALWAYS},
        )
        assert mode.id == "test-mode"
        assert mode.name == "Test Mode"
        assert mode.description == "A test mode"
        assert mode.tool_permissions["grep"] == ToolPermission.ALWAYS

    def test_mode_id_validation_lowercase_only(self):
        """Test that mode IDs must be lowercase."""
        with pytest.raises(ValidationError, match="Invalid mode ID"):
            ModeConfig(
                id="Test-Mode",  # Contains uppercase
                name="Test",
                description="Test",
            )

    def test_mode_id_validation_alphanumeric_hyphens(self):
        """Test that mode IDs must be alphanumeric with hyphens."""
        # Valid IDs
        valid_ids = ["test", "test-mode", "test-mode-123", "mode123"]
        for mode_id in valid_ids:
            mode = ModeConfig(id=mode_id, name="Test", description="Test")
            assert mode.id == mode_id

        # Invalid IDs
        invalid_ids = ["test_mode", "test mode", "test.mode", "test@mode"]
        for mode_id in invalid_ids:
            with pytest.raises(ValidationError, match="Invalid mode ID"):
                ModeConfig(id=mode_id, name="Test", description="Test")

    def test_mode_id_max_length(self):
        """Test mode ID length limit."""
        with pytest.raises(ValidationError, match="too long"):
            ModeConfig(
                id="a" * 51,  # Exceeds 50 char limit
                name="Test",
                description="Test",
            )

    def test_mode_name_required(self):
        """Test that mode name is required and non-empty."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            ModeConfig(id="test", name="", description="Test")

        with pytest.raises(ValidationError, match="cannot be empty"):
            ModeConfig(id="test", name="   ", description="Test")

    def test_mode_name_max_length(self):
        """Test mode name length limit."""
        with pytest.raises(ValidationError, match="too long"):
            ModeConfig(
                id="test",
                name="a" * 101,  # Exceeds 100 char limit
                description="Test",
            )

    def test_ui_indicator_max_length(self):
        """Test UI indicator length limit."""
        with pytest.raises(ValidationError, match="too long"):
            ModeConfig(
                id="test",
                name="Test",
                description="Test",
                ui_indicator="⏵" * 11,  # Exceeds 10 char limit
            )

    def test_border_color_validation(self):
        """Test border color must be valid theme color."""
        # Valid colors
        valid_colors = ["primary", "secondary", "warning", "error", "success", "info"]
        for color in valid_colors:
            mode = ModeConfig(
                id="test", name="Test", description="Test", border_color=color
            )
            assert mode.border_color == color

        # Invalid color
        with pytest.raises(ValidationError, match="Invalid border_color"):
            ModeConfig(
                id="test", name="Test", description="Test", border_color="invalid-color"
            )

    def test_get_tool_permission_exact_match(self):
        """Test getting tool permission with exact match."""
        mode = ModeConfig(
            id="test",
            name="Test",
            description="Test",
            tool_permissions={
                "grep": ToolPermission.ALWAYS,
                "bash": ToolPermission.NEVER,
            },
        )
        assert mode.get_tool_permission("grep") == ToolPermission.ALWAYS
        assert mode.get_tool_permission("bash") == ToolPermission.NEVER
        assert mode.get_tool_permission("read_file") is None

    def test_get_tool_permission_wildcard(self):
        """Test getting tool permission with wildcard."""
        mode = ModeConfig(
            id="test",
            name="Test",
            description="Test",
            tool_permissions={"grep": ToolPermission.ALWAYS, "*": ToolPermission.NEVER},
        )
        # Exact match takes precedence
        assert mode.get_tool_permission("grep") == ToolPermission.ALWAYS
        # Wildcard applies to others
        assert mode.get_tool_permission("bash") == ToolPermission.NEVER
        assert mode.get_tool_permission("read_file") == ToolPermission.NEVER


class TestPathRestrictionConfig:
    """Test PathRestrictionConfig."""

    def test_default_config(self):
        """Test default path restriction config."""
        config = PathRestrictionConfig()
        assert config.restrict_to_workdir is False
        assert config.allowed_patterns == ["**/*"]
        assert config.denied_patterns == []

    def test_custom_config(self):
        """Test custom path restriction config."""
        config = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=["**/*.py", "**/*.md"],
            denied_patterns=["**/secrets/**", "**/.env"],
        )
        assert config.restrict_to_workdir is True
        assert "**/*.py" in config.allowed_patterns
        assert "**/secrets/**" in config.denied_patterns


class TestPredefinedModes:
    """Test predefined mode configurations."""

    def test_normal_mode(self):
        """Test normal mode configuration."""
        mode = PREDEFINED_MODES["normal"]
        assert mode.id == "normal"
        assert mode.name == "Normal"
        assert mode.tool_permissions == {}
        assert mode.path_restrictions is None
        assert mode.is_custom is False

    def test_auto_approve_mode(self):
        """Test auto-approve mode configuration."""
        mode = PREDEFINED_MODES["auto-approve"]
        assert mode.id == "auto-approve"
        assert mode.name == "Auto-Approve"
        assert mode.tool_permissions == {"*": ToolPermission.ALWAYS}
        assert mode.border_color == "error"
        assert mode.is_custom is False

    def test_accept_edits_mode(self):
        """Test accept-edits mode configuration."""
        mode = PREDEFINED_MODES["accept-edits"]
        assert mode.id == "accept-edits"
        assert mode.name == "Accept Edits"
        assert mode.tool_permissions["grep"] == ToolPermission.ALWAYS
        assert mode.tool_permissions["read_file"] == ToolPermission.ALWAYS
        assert mode.path_restrictions is not None
        assert mode.path_restrictions.restrict_to_workdir is True
        assert mode.is_custom is False

    def test_plan_mode(self):
        """Test plan mode configuration."""
        mode = PREDEFINED_MODES["plan"]
        assert mode.id == "plan"
        assert mode.name == "Plan"
        assert mode.tool_permissions["grep"] == ToolPermission.ALWAYS
        assert mode.tool_permissions["*"] == ToolPermission.NEVER
        assert mode.is_custom is False

    def test_all_predefined_modes_present(self):
        """Test all predefined modes are in registry."""
        assert "normal" in PREDEFINED_MODES
        assert "auto-approve" in PREDEFINED_MODES
        assert "accept-edits" in PREDEFINED_MODES
        assert "plan" in PREDEFINED_MODES
        assert len(PREDEFINED_MODES) == 4


class TestModeRegistry:
    """Test mode registry building and management."""

    def test_registry_initialized_with_predefined(self):
        """Test that PREDEFINED_MODES contains all expected modes."""
        assert len(PREDEFINED_MODES) == 4
        assert "normal" in PREDEFINED_MODES
        assert "auto-approve" in PREDEFINED_MODES
        assert "accept-edits" in PREDEFINED_MODES
        assert "plan" in PREDEFINED_MODES

    def test_build_mode_registry_empty_custom(self):
        """Test building registry with no custom modes."""
        registry = build_mode_registry({})
        assert len(registry) == 4
        assert all(mode_id in registry for mode_id in PREDEFINED_MODES.keys())

    def test_build_mode_registry_with_custom(self):
        """Test building registry with custom modes."""
        custom_modes = {
            "my-mode": {
                "name": "My Mode",
                "description": "Custom mode",
                "tool_permissions": {"grep": "always"},
            }
        }
        registry = build_mode_registry(custom_modes)
        assert len(registry) == 5
        assert "my-mode" in registry
        assert registry["my-mode"].name == "My Mode"
        assert registry["my-mode"].is_custom is True

    def test_build_mode_registry_rejects_override(self):
        """Test that overriding predefined modes raises error."""
        custom_modes = {
            "normal": {  # Try to override predefined mode
                "name": "Custom Normal",
                "description": "Override",
            }
        }
        with pytest.raises(ValueError, match="Cannot override predefined mode"):
            build_mode_registry(custom_modes)

    def test_build_mode_registry_invalid_mode_raises(self):
        """Test that invalid custom mode raises error."""
        custom_modes = {
            "invalid_mode": {  # Invalid ID (underscore)
                "name": "Invalid",
                "description": "Test",
            }
        }
        with pytest.raises(ValueError, match="Invalid custom mode"):
            build_mode_registry(custom_modes)

    def test_get_mode_config_valid(self):
        """Test getting a valid mode config."""
        registry = build_mode_registry({})
        mode = get_mode_config("normal", registry)
        assert mode.id == "normal"

    def test_get_mode_config_invalid(self):
        """Test getting invalid mode raises error."""
        registry = build_mode_registry({})
        with pytest.raises(ValueError, match="Unknown mode"):
            get_mode_config("nonexistent-mode", registry)

    def test_list_available_modes_order(self):
        """Test that modes are listed in correct order."""
        # Test with only predefined modes
        registry = build_mode_registry({})
        modes = list_available_modes(registry)
        mode_ids = [m.id for m in modes]

        # First 4 should be predefined in order (safe → dangerous)
        assert mode_ids[0] == "normal"
        assert mode_ids[1] == "plan"
        assert mode_ids[2] == "accept-edits"
        assert mode_ids[3] == "auto-approve"

    def test_list_available_modes_with_custom(self):
        """Test listing modes with custom modes appended."""
        custom_modes = {
            "zebra-mode": {"name": "Zebra", "description": "Test"},
            "alpha-mode": {"name": "Alpha", "description": "Test"},
        }
        registry = build_mode_registry(custom_modes)
        modes = list_available_modes(registry)
        mode_ids = [m.id for m in modes]

        # Predefined modes first (safe → dangerous)
        assert mode_ids[0:4] == ["normal", "plan", "accept-edits", "auto-approve"]
        # Custom modes alphabetically after
        assert mode_ids[4] == "alpha-mode"
        assert mode_ids[5] == "zebra-mode"
