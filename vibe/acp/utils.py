from __future__ import annotations

from enum import StrEnum

from acp.schema import (
    ContentToolCallContent,
    Implementation,
    PermissionOption,
    PermissionOptionKind,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionMode,
    SessionModeState,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from vibe.app_server.config import THINKING_LEVELS, ConfigView, ProxySettingsView
from vibe.app_server.models import AgentSummary, AgentType, RequiredPermission


class ToolOption(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    ALLOW_ALWAYS_PERMANENT = "allow_always_permanent"
    REJECT_ONCE = "reject_once"


_KIND_ALLOW_ONCE: PermissionOptionKind = "allow_once"
_KIND_ALLOW_ALWAYS: PermissionOptionKind = "allow_always"
_KIND_REJECT_ONCE: PermissionOptionKind = "reject_once"


def build_permission_options(
    required_permissions: list[RequiredPermission],
) -> list[PermissionOption]:
    # The webview parses these snake_case keys (invocation_pattern,
    # session_pattern); RequiredPermission has a camel alias generator, so
    # dumping by_alias would emit camelCase and blank the displayed patterns.
    permissions_meta = [
        permission.model_dump(mode="json") for permission in required_permissions
    ]
    session_meta = (
        {"required_permissions": permissions_meta} if permissions_meta else None
    )
    return [
        PermissionOption(
            option_id=ToolOption.ALLOW_ONCE, name="Allow once", kind=_KIND_ALLOW_ONCE
        ),
        PermissionOption(
            option_id=ToolOption.ALLOW_ALWAYS,
            name="Allow for remainder of this session",
            kind=_KIND_ALLOW_ALWAYS,
            field_meta=session_meta,
        ),
        PermissionOption(
            option_id=ToolOption.ALLOW_ALWAYS_PERMANENT,
            name="Always allow",
            kind=_KIND_ALLOW_ALWAYS,
            field_meta=session_meta,
        ),
        PermissionOption(
            option_id=ToolOption.REJECT_ONCE, name="Deny", kind=_KIND_REJECT_ONCE
        ),
    ]


def is_jetbrains_client(client_info: Implementation | None) -> bool:
    return bool(client_info and client_info.name.startswith("JetBrains."))


def build_mode_state(
    agents: list[AgentSummary], active: AgentSummary
) -> tuple[SessionModeState, SessionConfigOptionSelect]:
    primary = [agent for agent in agents if agent.agent_type is AgentType.AGENT]
    modes = [
        SessionMode(
            id=agent.name, name=agent.display_name, description=agent.description
        )
        for agent in primary
    ]
    options = [
        SessionConfigSelectOption(
            value=agent.name, name=agent.display_name, description=agent.description
        )
        for agent in primary
    ]
    return (
        SessionModeState(current_mode_id=active.name, available_modes=modes),
        SessionConfigOptionSelect(
            id="mode",
            name="Session Mode",
            current_value=active.name,
            category="mode",
            type="select",
            options=options,
        ),
    )


def build_model_config(config: ConfigView) -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        id="model",
        name="Model",
        current_value=config.active_model.alias,
        category="model",
        type="select",
        options=[
            SessionConfigSelectOption(
                value=model.alias, name=model.display_name, description=model.name
            )
            for model in config.models
        ],
    )


def make_thinking_response(config: ConfigView) -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        id="thinking",
        name="Thinking",
        current_value=config.active_model.thinking,
        category="thinking",
        type="select",
        options=[
            SessionConfigSelectOption(value=level, name=level.capitalize())
            for level in THINKING_LEVELS
        ],
    )


def compact_start_update(tool_call_id: str) -> ToolCallStart:
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=tool_call_id,
        title="Compacting conversation history...",
        kind="other",
        status="in_progress",
        content=[
            ContentToolCallContent(
                type="content",
                content=TextContentBlock(
                    type="text",
                    text=(
                        "Automatic context management, no approval required. "
                        "This may take some time..."
                    ),
                ),
            )
        ],
    )


def compact_end_update(tool_call_id: str, message: str) -> ToolCallProgress:
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=tool_call_id,
        title="Compacted conversation history",
        status="completed",
        content=[
            ContentToolCallContent(
                type="content", content=TextContentBlock(type="text", text=message)
            )
        ],
    )


def compact_error_update(tool_call_id: str, message: str) -> ToolCallProgress:
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=tool_call_id,
        title="Compaction failed",
        status="failed",
        raw_output=message,
    )


def get_proxy_help_text(settings: ProxySettingsView) -> str:
    lines = [
        "## Proxy Configuration",
        "",
        "Configure proxy and SSL settings for HTTP requests.",
        "",
        "### Usage:",
        "- `/proxy-setup` - Show this help and current settings",
        "- `/proxy-setup KEY value` - Set an environment variable",
        "- `/proxy-setup KEY` - Remove an environment variable",
        "",
        "### Supported Variables:",
        *[
            f"- `{key}`: {description}"
            for key, description in settings.descriptions.items()
        ],
        "",
        "### Current Settings:",
    ]
    configured = [
        f"- `{key}={value}`" for key, value in settings.values.items() if value
    ]
    lines.extend(configured or ["- (none configured)"])
    return "\n".join(lines)
