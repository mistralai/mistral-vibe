from __future__ import annotations

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.paths import PLANS_DIR
from vibe.core.tools.base import ToolPermission


class TestPlanAgentResolvePermission:
    """Plan agent overrides write_file/edit to NEVER with allowlist=[plans/*]
    and allowlists read_file on [plans/*]. resolve_permission must reflect this.
    """

    def test_write_file_to_non_plan_path_denied_in_plan_mode(self) -> None:
        config = build_test_vibe_config()
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        tool = agent.tool_manager.get("write_file")
        from vibe.core.tools.builtins.write_file import WriteFileArgs

        args = WriteFileArgs(file_path="/some/random/file.py", content="hello")

        ctx = tool.resolve_permission(args)

        # With plan agent override: permission should be NEVER
        # (unless the path matches the plans allowlist)
        assert ctx is not None
        assert ctx.permission == ToolPermission.NEVER

    def test_write_file_to_plan_path_allowed_in_plan_mode(self) -> None:
        config = build_test_vibe_config()
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        tool = agent.tool_manager.get("write_file")
        from vibe.core.tools.builtins.write_file import WriteFileArgs

        plan_path = str(PLANS_DIR.path / "my-plan.md")
        args = WriteFileArgs(file_path=plan_path, content="# Plan")

        ctx = tool.resolve_permission(args)

        # Plan path is in the allowlist, so should be ALWAYS
        assert ctx is not None
        assert ctx.permission == ToolPermission.ALWAYS

    def test_edit_to_non_plan_path_denied_in_plan_mode(self) -> None:
        config = build_test_vibe_config()
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        tool = agent.tool_manager.get("edit")
        from vibe.core.tools.builtins.edit import EditArgs

        args = EditArgs(file_path="/some/file.py", old_string="a", new_string="b")

        ctx = tool.resolve_permission(args)

        assert ctx is not None
        assert ctx.permission == ToolPermission.NEVER

    def test_edit_to_plan_path_allowed_in_plan_mode(self) -> None:
        config = build_test_vibe_config()
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        tool = agent.tool_manager.get("edit")
        from vibe.core.tools.builtins.edit import EditArgs

        plan_path = str(PLANS_DIR.path / "my-plan.md")
        args = EditArgs(file_path=plan_path, old_string="a", new_string="b")

        ctx = tool.resolve_permission(args)

        assert ctx is not None
        assert ctx.permission == ToolPermission.ALWAYS

    def test_read_file_to_plan_path_allowed_in_plan_mode(self) -> None:
        config = build_test_vibe_config()
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        tool = agent.tool_manager.get("read_file")
        from vibe.core.tools.builtins.read_file import ReadFileArgs

        plan_path = str(PLANS_DIR.path / "my-plan.md")
        args = ReadFileArgs(file_path=plan_path)

        ctx = tool.resolve_permission(args)

        # Plan path is in the allowlist, so should be ALWAYS even though
        # PLANS_DIR lives outside the workdir.
        assert ctx is not None
        assert ctx.permission == ToolPermission.ALWAYS


class TestAcceptEditsAgentResolvePermission:
    """Accept-edits agent sets write_file/edit to ALWAYS.
    resolve_permission must reflect this.
    """

    def test_write_file_always_in_accept_edits_mode(self) -> None:
        config = build_test_vibe_config()
        agent = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.ACCEPT_EDITS
        )

        tool = agent.tool_manager.get("write_file")
        from vibe.core.tools.builtins.write_file import WriteFileArgs

        # Use a workdir-relative path; outside-workdir always requires ASK
        # regardless of agent permission.
        args = WriteFileArgs(file_path="file.py", content="hello")

        ctx = tool.resolve_permission(args)

        # Inside workdir, no allowlist/denylist/sensitive match → None,
        # so the caller falls through to config permission (ALWAYS).
        assert ctx is None


class TestAgentOverrideNotLeakedAcrossSwitches:
    """Switching agents must change what resolve_permission returns."""

    def test_switch_from_plan_to_default_restores_write_permission(self) -> None:
        config = build_test_vibe_config()
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        tool = agent.tool_manager.get("write_file")
        from vibe.core.tools.builtins.write_file import WriteFileArgs

        args = WriteFileArgs(file_path="/some/file.py", content="hello")

        # In plan mode: should be NEVER
        ctx_plan = tool.resolve_permission(args)
        assert ctx_plan is not None
        assert ctx_plan.permission == ToolPermission.NEVER

        # Switch to default
        agent.agent_manager.switch_profile(BuiltinAgentName.DEFAULT)

        # In default mode: should NOT be NEVER
        ctx_default = tool.resolve_permission(args)
        assert ctx_default is not None
        assert ctx_default.permission != ToolPermission.NEVER
