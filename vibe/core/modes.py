"""Mode system for Vibe - declarative config-driven operational modes."""

from __future__ import annotations

from enum import StrEnum
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from vibe.core.tools.base import ToolPermission


class ModeID(StrEnum):
    """Standard mode identifiers."""

    NORMAL = "normal"
    AUTO_APPROVE = "auto-approve"
    ACCEPT_EDITS = "accept-edits"
    PLAN = "plan"


# Constants for validation limits
MAX_MODE_ID_LENGTH = 50
MAX_MODE_NAME_LENGTH = 100
MAX_UI_INDICATOR_LENGTH = 10

# Valid Textual theme colors for border styling
VALID_BORDER_COLORS = {
    "primary",
    "secondary",
    "accent",
    "warning",
    "error",
    "success",
    "info",
    "surface",
    "panel",
}


class PathRestrictionConfig(BaseModel):
    """Configuration for path-based restrictions in a mode."""

    restrict_to_workdir: bool = False
    allowed_patterns: list[str] = Field(default_factory=lambda: ["**/*"])
    denied_patterns: list[str] = Field(default_factory=list)


class ModeConfig(BaseModel):
    """Configuration for a specific operational mode."""

    id: str
    name: str
    description: str
    tool_permissions: dict[str, ToolPermission] = Field(default_factory=dict)
    path_restrictions: PathRestrictionConfig | None = None
    ui_indicator: str = Field(default="⏵")
    border_color: str = Field(default="primary")
    is_custom: bool = Field(default=False, exclude=True)

    @field_validator("id", mode="after")
    @classmethod
    def validate_mode_id(cls, v: str) -> str:
        """Validate mode ID format: lowercase alphanumeric with hyphens."""
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", v):
            raise ValueError(
                "Invalid mode ID '"
                + v
                + "'. Must be lowercase alphanumeric with hyphens "
                "(e.g., 'my-custom-mode')"
            )
        if len(v) > MAX_MODE_ID_LENGTH:
            raise ValueError(
                "Mode ID '"
                + v
                + "' too long (max "
                + str(MAX_MODE_ID_LENGTH)
                + " characters)"
            )
        return v

    @field_validator("name", mode="after")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate mode name is non-empty and reasonable length."""
        if not v or len(v.strip()) == 0:
            raise ValueError("Mode name cannot be empty")
        if len(v) > MAX_MODE_NAME_LENGTH:
            raise ValueError(
                "Mode name too long (max " + str(MAX_MODE_NAME_LENGTH) + " characters)"
            )
        return v.strip()

    @field_validator("ui_indicator", mode="after")
    @classmethod
    def validate_ui_indicator(cls, v: str) -> str:
        """Validate UI indicator length."""
        if len(v) > MAX_UI_INDICATOR_LENGTH:
            raise ValueError(
                "UI indicator too long (max "
                + str(MAX_UI_INDICATOR_LENGTH)
                + " characters)"
            )
        return v

    @field_validator("border_color", mode="after")
    @classmethod
    def validate_border_color(cls, v: str) -> str:
        """Validate border color is a valid Textual theme color."""
        if v not in VALID_BORDER_COLORS:
            raise ValueError(
                "Invalid border_color '"
                + v
                + "'. Must be one of: "
                + ", ".join(sorted(VALID_BORDER_COLORS))
            )
        return v

    def get_tool_permission(self, tool_name: str) -> ToolPermission | None:
        """Get permission for a specific tool, with wildcard support."""
        # Check exact match first
        if tool_name in self.tool_permissions:
            return self.tool_permissions[tool_name]
        # Check wildcard
        if "*" in self.tool_permissions:
            return self.tool_permissions["*"]
        return None

    @property
    def is_dangerous(self) -> bool:
        """Check if this mode auto-approves potentially dangerous tools."""
        return self.tool_permissions.get("*") == ToolPermission.ALWAYS


# Predefined mode configurations

NORMAL_MODE = ModeConfig(
    id=ModeID.NORMAL,
    name="Normal",
    description="Standard operation with manual approval for restricted tools",
    tool_permissions={},  # Use tool defaults
    path_restrictions=None,
    ui_indicator="⏵",
    border_color="primary",
    is_custom=False,
)

AUTO_APPROVE_MODE = ModeConfig(
    id=ModeID.AUTO_APPROVE,
    name="Auto-Approve",
    description="Automatically approves all tool executions",
    tool_permissions={"*": ToolPermission.ALWAYS},
    path_restrictions=None,
    ui_indicator="⏵⏵⏵",
    border_color="error",
    is_custom=False,
)

ACCEPT_EDITS_MODE = ModeConfig(
    id=ModeID.ACCEPT_EDITS,
    name="Accept Edits",
    description="Auto-approves file editing tools within workdir, requires approval for others",
    tool_permissions={
        "grep": ToolPermission.ALWAYS,
        "read_file": ToolPermission.ALWAYS,
        "search_replace": ToolPermission.ALWAYS,
        "todo": ToolPermission.ALWAYS,
        "write_file": ToolPermission.ALWAYS,
    },
    path_restrictions=PathRestrictionConfig(
        restrict_to_workdir=True, allowed_patterns=["**/*"], denied_patterns=[]
    ),
    ui_indicator="⏵⏵",
    border_color="warning",
    is_custom=False,
)

PLAN_MODE = ModeConfig(
    id=ModeID.PLAN,
    name="Plan",
    description="Read-only mode for planning and analysis",
    tool_permissions={
        # Explicitly allow read-only tools
        "grep": ToolPermission.ALWAYS,
        "read_file": ToolPermission.ALWAYS,
        "todo": ToolPermission.ALWAYS,
        # Deny all other tools
        "*": ToolPermission.NEVER,
    },
    path_restrictions=None,
    ui_indicator="⏸",
    border_color="primary",
    is_custom=False,
)

# Registry of predefined modes (immutable)
PREDEFINED_MODES: dict[str, ModeConfig] = {
    ModeID.NORMAL: NORMAL_MODE,
    ModeID.AUTO_APPROVE: AUTO_APPROVE_MODE,
    ModeID.ACCEPT_EDITS: ACCEPT_EDITS_MODE,
    ModeID.PLAN: PLAN_MODE,
}


def build_mode_registry(
    custom_modes_config: dict[str, dict[str, Any]],
) -> dict[str, ModeConfig]:
    """Build the complete mode registry from predefined and custom modes."""
    registry = dict(PREDEFINED_MODES)  # Start with predefined modes

    # Add custom modes
    for mode_id, mode_data in custom_modes_config.items():
        # Check for reserved mode IDs
        if mode_id in PREDEFINED_MODES:
            raise ValueError(
                "Cannot override predefined mode '" + mode_id + "'. "
                "Predefined modes are: " + ", ".join(PREDEFINED_MODES.keys())
            )

        try:
            # Parse and validate custom mode
            mode_config = ModeConfig.model_validate({
                **mode_data,
                "id": mode_id,
                "is_custom": True,
            })
            registry[mode_id] = mode_config
        except Exception as e:
            raise ValueError(
                "Invalid custom mode configuration for '" + mode_id + "': " + str(e)
            ) from e

    return registry


def get_mode_config(mode_id: str, mode_registry: dict[str, ModeConfig]) -> ModeConfig:
    """Get mode configuration by ID."""
    if mode_id not in mode_registry:
        available = ", ".join("'" + m + "'" for m in sorted(mode_registry.keys()))
        raise ValueError(
            "Unknown mode: '" + mode_id + "'. Available modes: " + available
        )
    return mode_registry[mode_id]


def list_available_modes(mode_registry: dict[str, ModeConfig]) -> list[ModeConfig]:
    """Get list of all available modes."""
    # Predefined modes in fixed order (safe → dangerous)
    predefined_order = (
        ModeID.NORMAL,
        ModeID.PLAN,
        ModeID.ACCEPT_EDITS,
        ModeID.AUTO_APPROVE,
    )
    result = [
        mode_registry[mode_id]
        for mode_id in predefined_order
        if mode_id in mode_registry
    ]

    # Custom modes in alphabetical order (single pass, minimal allocations)
    predefined_set = frozenset(predefined_order)
    result.extend(
        mode_registry[mode_id]
        for mode_id in sorted(mode_registry.keys())
        if mode_id not in predefined_set
    )

    return result
