"""Unit coverage for the runtime snapshot's auto-approve (YOLO) policy clamp and
the session-start credential gate.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._runtime import (
    _effective_bypass_tool_permissions,
    _require_active_provider_credential,
    build_unified_runtime_snapshot,
)
from vibe.core.agents.manager import AgentManager
from vibe.core.config import MissingAPIKeyError, ModelConfig, VibeConfigSchema
from vibe.utils.api_keys import session_credentials


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


class _SignIn:
    """A connection credential that knows, offline, whether a user signed in."""

    def __init__(self, *, signed_in: bool) -> None:
        self._signed_in = signed_in
        self.env_vars: list[str] = []

    def __call__(self, env_var: str) -> _SignIn:
        self.env_vars.append(env_var)
        return self

    async def resolve(self, target: Any) -> Any:
        raise AssertionError("the gate must not reach the network")

    async def reject(self, target: Any, **_: Any) -> Any:
        raise AssertionError("the gate must not reach the network")

    def has_credential(self) -> bool:
        return self._signed_in

    def cached_principal(self) -> str | None:
        return None


_OTHER_PROVIDER = {
    "name": "other",
    "api_base": "https://other.example/v1",
    "api_key_env_var": "OTHER_API_KEY",
    "api_style": "openai",
    "backend": "generic",
}


class TestRequireActiveProviderCredential:
    def test_a_signed_in_connection_needs_no_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        sign_in = _SignIn(signed_in=True)

        _require_active_provider_credential(build_test_vibe_config(), sign_in, None)

        assert sign_in.env_vars == ["MISTRAL_API_KEY"]

    def test_a_signed_out_connection_without_a_key_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

        with pytest.raises(MissingAPIKeyError) as raised:
            _require_active_provider_credential(
                build_test_vibe_config(), _SignIn(signed_in=False), None
            )

        assert raised.value.env_key == "MISTRAL_API_KEY"

    def test_a_third_party_provider_still_needs_its_own_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OTHER_API_KEY", raising=False)
        config = build_test_vibe_config(
            providers=[_OTHER_PROVIDER],
            models=[ModelConfig(name="other-model", provider="other", alias="o")],
            active_model="o",
        )
        sign_in = _SignIn(signed_in=True)

        with pytest.raises(MissingAPIKeyError) as raised:
            _require_active_provider_credential(config, sign_in, None)

        assert raised.value.env_key == "OTHER_API_KEY"
        assert sign_in.env_vars == []

    def test_a_runs_own_key_is_checked_as_a_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        sign_in = _SignIn(signed_in=False)
        run_keys = {"MISTRAL_API_KEY": "run-key"}

        # As the multiplexed channel serves the run: its keys resolve first.
        with session_credentials(run_keys):
            _require_active_provider_credential(
                build_test_vibe_config(), sign_in, run_keys
            )

        assert sign_in.env_vars == []
