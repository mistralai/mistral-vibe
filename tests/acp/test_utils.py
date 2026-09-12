from __future__ import annotations

from vibe.acp.utils import build_mode_state, build_permission_options
from vibe.agents import AgentSafety, AgentType
from vibe.app_server.models import AgentSummary
from vibe.permissions import PermissionScope, RequiredPermission


def test_build_permission_options_serializes_scopes_with_snake_case_keys() -> None:
    required = [
        RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="pnpm add --save-dev vitest",
            session_pattern="pnpm *",
            label="pnpm *",
        )
    ]

    options = build_permission_options(required)

    session_options = [option for option in options if option.kind == "allow_always"]
    assert session_options
    expected_meta = [
        {
            "scope": "command_pattern",
            "invocation_pattern": "pnpm add --save-dev vitest",
            "session_pattern": "pnpm *",
            "label": "pnpm *",
        }
    ]
    for option in session_options:
        assert option.field_meta is not None
        assert option.field_meta["required_permissions"] == expected_meta


def _agent(
    name: str,
    *,
    safety: AgentSafety = AgentSafety.NEUTRAL,
    agent_type: AgentType = AgentType.AGENT,
) -> AgentSummary:
    return AgentSummary(
        name=name,
        display_name=name.replace("-", " ").title(),
        description=f"{name} mode",
        safety=safety,
        agent_type=agent_type,
    )


def test_build_mode_state_includes_active_mode_hidden_from_picker() -> None:
    # A session can run a mode the rollout gate hides from the offered list
    # (e.g. smart-approve after its experiment flag lapses). The advertised list
    # must still contain the active mode so the client can label it and switch
    # away from it.
    offered = [_agent("ask"), _agent("auto-approve", safety=AgentSafety.YOLO)]
    active = _agent("smart-approve", safety=AgentSafety.SMART)

    state, option = build_mode_state(offered, active)

    mode_ids = [mode.id for mode in state.available_modes]
    assert state.current_mode_id == "smart-approve"
    assert state.current_mode_id in mode_ids
    option_values = [choice.value for choice in option.options]
    assert option.current_value == "smart-approve"
    assert option.current_value in option_values


def test_build_mode_state_does_not_duplicate_active_mode() -> None:
    auto = _agent("auto-approve", safety=AgentSafety.YOLO)
    offered = [_agent("ask"), auto]

    state, option = build_mode_state(offered, auto)

    mode_ids = [mode.id for mode in state.available_modes]
    assert mode_ids.count("auto-approve") == 1
    option_values = [choice.value for choice in option.options]
    assert option_values.count("auto-approve") == 1


def test_build_mode_state_excludes_subagents_but_keeps_active() -> None:
    offered = [
        _agent("ask"),
        _agent("explore", safety=AgentSafety.SAFE, agent_type=AgentType.SUBAGENT),
    ]
    active = _agent("smart-approve", safety=AgentSafety.SMART)

    state, _ = build_mode_state(offered, active)

    mode_ids = [mode.id for mode in state.available_modes]
    assert "explore" not in mode_ids
    assert "smart-approve" in mode_ids
