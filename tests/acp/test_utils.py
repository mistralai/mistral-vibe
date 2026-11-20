from __future__ import annotations

from vibe.acp.utils import (
    ToolOption,
    build_agent_config,
    build_mode_state,
    build_permission_options,
)
from vibe.agents import AgentSafety, AgentType
from vibe.app_server.models import AgentSummary, ApprovalPolicySummary
from vibe.permissions import PathGrantScope, PermissionScope, RequiredPermission


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


def test_build_permission_options_exposes_exact_file_scope() -> None:
    required = [
        RequiredPermission(
            scope=PermissionScope.OUTSIDE_DIRECTORY,
            invocation_pattern="/outside/config.json",
            session_pattern="vibe-path:exact:/outside/config.json",
            label="outside workdir (/outside/config.json)",
        )
    ]

    options = build_permission_options(required, [PathGrantScope.EXACT])

    assert [option.option_id for option in options] == [
        ToolOption.ALLOW_ONCE,
        ToolOption.ALLOW_SESSION_EXACT,
        ToolOption.ALLOW_PERMANENT_EXACT,
        ToolOption.REJECT_ONCE,
    ]
    assert [option.name for option in options] == [
        "Allow once",
        "Allow this file only for this session",
        "Always allow this file",
        "Deny",
    ]


def test_build_permission_options_exposes_recursive_folder_scope() -> None:
    options = build_permission_options([], [PathGrantScope.DIRECTORY_RECURSIVE])

    assert [option.name for option in options] == [
        "Allow once",
        "Allow this folder for this session",
        "Always allow this folder",
        "Deny",
    ]


def test_build_permission_options_names_every_recursive_folder() -> None:
    required = [
        RequiredPermission(
            scope=PermissionScope.OUTSIDE_DIRECTORY,
            invocation_pattern=path,
            session_pattern=f"vibe-path:exact:{path}",
            label=f"outside workdir ({path})",
            path_scope_root=path,
        )
        for path in ("/etc", "/var/log")
    ]

    options = build_permission_options(required, [PathGrantScope.DIRECTORY_RECURSIVE])

    assert [option.name for option in options] == [
        "Allow once",
        "Allow these folders for this session",
        "Always allow these folders",
        "Deny",
    ]


def _policy(
    name: str, *, safety: AgentSafety = AgentSafety.NEUTRAL
) -> ApprovalPolicySummary:
    return ApprovalPolicySummary(
        name=name,
        display_name=name.replace("-", " ").title(),
        description=f"{name} policy",
        safety=safety,
    )


def _agent(name: str, *, agent_type: AgentType = AgentType.AGENT) -> AgentSummary:
    return AgentSummary(
        name=name,
        display_name=name.replace("-", " ").title(),
        description=f"{name} agent",
        agent_type=agent_type,
    )


def test_build_mode_state_includes_active_mode_hidden_from_picker() -> None:
    # A session can run a policy the rollout gate hides from the offered list
    # (e.g. smart-approve after its experiment flag lapses). The advertised list
    # must still contain the active one so the client can label it and switch
    # away from it.
    offered = [_policy("ask"), _policy("auto-approve", safety=AgentSafety.YOLO)]
    active = _policy("smart-approve", safety=AgentSafety.SMART)

    state, option = build_mode_state(offered, active)

    mode_ids = [mode.id for mode in state.available_modes]
    assert state.current_mode_id == "smart-approve"
    assert state.current_mode_id in mode_ids
    option_values = [choice.value for choice in option.options]
    assert option.current_value == "smart-approve"
    assert option.current_value in option_values


def test_build_mode_state_does_not_duplicate_active_mode() -> None:
    auto = _policy("auto-approve", safety=AgentSafety.YOLO)
    offered = [_policy("ask"), auto]

    state, option = build_mode_state(offered, auto)

    mode_ids = [mode.id for mode in state.available_modes]
    assert mode_ids.count("auto-approve") == 1
    option_values = [choice.value for choice in option.options]
    assert option_values.count("auto-approve") == 1


def test_build_agent_config_lists_primary_agents_only() -> None:
    offered = [
        _agent("default"),
        _agent("explore", agent_type=AgentType.SUBAGENT),
        _agent("task-lead"),
    ]

    option = build_agent_config(offered, offered[2])

    assert option is not None
    assert option.id == "agent"
    assert option.current_value == "task-lead"
    assert [choice.value for choice in option.options] == ["default", "task-lead"]


def test_build_agent_config_is_left_out_with_one_agent() -> None:
    offered = [_agent("default"), _agent("explore", agent_type=AgentType.SUBAGENT)]

    assert build_agent_config(offered, offered[0]) is None
