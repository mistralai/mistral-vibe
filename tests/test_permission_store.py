from __future__ import annotations

from pydantic import BaseModel
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.permissions import (
    ApprovedRule,
    PermissionScope,
    PermissionStore,
    RequiredPermission,
)
from vibe.core.types import ApprovalResponse, FunctionCall, ToolCall, ToolResultEvent


class TestPermissionStore:
    def test_covers_returns_false_when_empty(self):
        store = PermissionStore()
        rp = RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="npm install foo",
            session_pattern="npm install *",
            label="npm install *",
        )

        assert not store.covers("bash", rp)

    def test_covers_returns_true_after_matching_rule_added(self):
        store = PermissionStore()
        store.add_rule(
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="npm install *",
            )
        )
        rp = RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="npm install foo",
            session_pattern="npm install *",
            label="npm install *",
        )

        assert store.covers("bash", rp)

    def test_covers_isolates_by_tool_name(self):
        store = PermissionStore()
        store.add_rule(
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="npm install *",
            )
        )
        rp = RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="npm install foo",
            session_pattern="npm install *",
            label="npm install *",
        )

        assert not store.covers("read_file", rp)

    def test_tool_permission_round_trip(self):
        store = PermissionStore()
        assert store.get_tool_permission("bash") is None

        store.set_tool_permission("bash", ToolPermission.ALWAYS)

        assert store.get_tool_permission("bash") == ToolPermission.ALWAYS


class TestAgentLoopSharesStore:
    def test_subagent_inherits_parent_session_rules(self):
        store = PermissionStore()
        parent = build_test_agent_loop(permission_store=store)
        subagent = build_test_agent_loop(permission_store=store)

        parent.approve_always(
            "bash",
            [
                RequiredPermission(
                    scope=PermissionScope.COMMAND_PATTERN,
                    invocation_pattern="npm install foo",
                    session_pattern="npm install *",
                    label="npm install *",
                )
            ],
        )

        rp = RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="npm install bar",
            session_pattern="npm install *",
            label="npm install *",
        )
        assert subagent._permission_store.covers("bash", rp)

    def test_subagent_inherits_parent_tool_permission(self):
        store = PermissionStore()
        parent = build_test_agent_loop(permission_store=store)
        subagent = build_test_agent_loop(permission_store=store)

        parent.approve_always("bash", None)

        assert (
            subagent._permission_store.get_tool_permission("bash")
            == ToolPermission.ALWAYS
        )

    @pytest.mark.asyncio
    async def test_subagent_applies_parent_tool_permission_before_resolution(self):
        store = PermissionStore()
        parent = build_test_agent_loop(permission_store=store)
        parent.approve_always("bash", None)
        approval_requested = False

        async def approval_callback(
            tool_name: str,
            args: BaseModel,
            tool_call_id: str,
            required_permissions: list[RequiredPermission] | None,
        ) -> tuple[ApprovalResponse, str | None]:
            nonlocal approval_requested
            approval_requested = True
            return ApprovalResponse.NO, None

        tool_call = ToolCall(
            id="call_1",
            index=0,
            function=FunctionCall(name="bash", arguments='{"command":"true"}'),
        )
        subagent = build_test_agent_loop(
            config=build_test_vibe_config(enabled_tools=["bash"]),
            backend=FakeBackend([
                [mock_llm_chunk(content="Running it.", tool_calls=[tool_call])],
                [mock_llm_chunk(content="Done.")],
            ]),
            permission_store=store,
        )
        subagent.set_approval_callback(approval_callback)

        events = [event async for event in subagent.act("run true")]
        tool_results = [event for event in events if isinstance(event, ToolResultEvent)]

        assert not approval_requested
        assert len(tool_results) == 1
        assert tool_results[0].skipped is False
        assert "permission" not in subagent.config.tools.get("bash", {})

    def test_default_store_is_per_loop_when_not_shared(self):
        a = build_test_agent_loop()
        b = build_test_agent_loop()

        assert a._permission_store is not b._permission_store
        assert a._permission_store.lock is not b._permission_store.lock

    def test_subagent_shares_parent_approval_lock(self):
        store = PermissionStore()
        parent = build_test_agent_loop(permission_store=store)
        subagent = build_test_agent_loop(permission_store=store)

        assert parent._permission_store.lock is subagent._permission_store.lock
