"""Unit coverage for the auto-approve (YOLO) policy clamp in the runtime snapshot."""

from __future__ import annotations

from typing import Any, cast

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._runtime import (
    _effective_bypass_tool_permissions,
    build_unified_runtime_snapshot,
)
from vibe.core.agents.manager import AgentManager
from vibe.core.config import VibeConfigSchema


def _snapshot(config: VibeConfigSchema, *, auto_approve: bool = False):
    orchestrator = FakeConfigOrchestrator(config)
    return build_unified_runtime_snapshot(
        cast(Any, orchestrator),
        AgentManager(cast(Any, orchestrator)),
        auto_approve=auto_approve,
    )


class TestEffectiveBypassToolPermissions:
    def test_default_allows_the_flag(self) -> None:
        config = build_test_vibe_config()
        assert _effective_bypass_tool_permissions(config, True) is True

    def test_disabled_mode_clamps_the_flag(self) -> None:
        config = build_test_vibe_config(enable_auto_approve=False)
        assert _effective_bypass_tool_permissions(config, True) is False

    def test_disabled_mode_clamps_the_config_field(self) -> None:
        config = build_test_vibe_config(
            enable_auto_approve=False, bypass_tool_permissions=True
        )
        assert _effective_bypass_tool_permissions(config, False) is False

    def test_disabled_mode_keeps_normal_prompts(self) -> None:
        config = build_test_vibe_config(enable_auto_approve=False)
        assert _effective_bypass_tool_permissions(config, False) is False


class TestRuntimeSnapshotBypass:
    def test_snapshot_reports_the_flag(self) -> None:
        snapshot = _snapshot(build_test_vibe_config(), auto_approve=True)
        assert snapshot.bypass_tool_permissions is True

    def test_snapshot_reports_the_clamp_and_explains_it(self) -> None:
        config = build_test_vibe_config(enable_auto_approve=False)
        snapshot = _snapshot(config, auto_approve=True)

        assert snapshot.bypass_tool_permissions is False
        assert any("enable_auto_approve" in issue.message for issue in snapshot.issues)
        assert not any("admin-managed" in issue.message for issue in snapshot.issues)

    def test_snapshot_without_the_flag_has_no_clamp_issue(self) -> None:
        config = build_test_vibe_config(enable_auto_approve=False)
        snapshot = _snapshot(config)

        assert snapshot.bypass_tool_permissions is False
        assert not any(
            "enable_auto_approve" in issue.message for issue in snapshot.issues
        )
