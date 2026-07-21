from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from pathlib import Path
import ssl
import tomllib
from typing import Literal, TypedDict, Unpack
from unittest.mock import MagicMock, patch

import pytest
import tomli_w

from tests.conftest import ConfigBuilder, build_test_vibe_config
from vibe.core.config import (
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
    DEFAULT_PROVIDERS,
    ConnectorConfig,
    ModelConfig,
    ProviderConfig,
    VibeConfigSchema,
)
from vibe.core.config._migration import BASH_READ_ONLY_MIGRATION, migrate_config_layers
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.paths import VIBE_HOME
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.types import Backend
from vibe.core.utils.http import build_ssl_context, configure_ssl_context
from vibe.setup.onboarding.context import OnboardingContext


class _ProviderConfigOverrides(TypedDict, total=False):
    api_key_env_var: str
    browser_auth_base_url: str | None
    browser_auth_api_base_url: str | None
    api_style: str
    backend: Backend
    reasoning_field_name: str
    project_id: str
    region: str


class _ModelConfigOverrides(TypedDict, total=False):
    temperature: float
    input_price: float
    output_price: float
    thinking: Literal["off", "low", "medium", "high"]
    auto_compact_threshold: int


def _default_provider(name: str) -> ProviderConfig:
    return next(provider for provider in DEFAULT_PROVIDERS if provider.name == name)


def _custom_provider(**overrides: Unpack[_ProviderConfigOverrides]) -> ProviderConfig:
    return ProviderConfig(
        name="custom-provider", api_base="https://custom.example/v1", **overrides
    )


def _custom_model(**overrides: Unpack[_ModelConfigOverrides]) -> ModelConfig:
    return ModelConfig(
        name="custom-model",
        provider="custom-provider",
        alias="custom-model",
        **overrides,
    )


def _custom_provider_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "custom-provider",
        "api_base": "https://custom.example/v1",
        "api_key_env_var": "CUSTOM_API_KEY",
    }
    payload.update(overrides)
    return payload


def _custom_model_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "custom-model",
        "provider": "custom-provider",
        "alias": "custom-model",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def run_migration() -> Callable[[], None]:
    def _run() -> None:
        asyncio.run(migrate_config_layers([UserConfigLayer()]))

    return _run


class TestResolveConfigFile:
    def test_resolves_local_config_when_exists_and_folder_is_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        local_config_dir = tmp_path / ".vibe"
        local_config_dir.mkdir()
        local_config = local_config_dir / "config.toml"
        local_config.write_text('active_model = "test"', encoding="utf-8")

        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)

        reset_harness_files_manager()
        init_harness_files_manager("user", "project")
        from vibe.core.config.harness_files import get_harness_files_manager

        mgr = get_harness_files_manager()
        resolved = mgr.config_file
        assert resolved is not None
        assert resolved == local_config
        assert resolved.is_file()
        assert resolved.read_text(encoding="utf-8") == 'active_model = "test"'

    def test_resolves_global_config_when_folder_is_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        local_config_dir = tmp_path / ".vibe"
        local_config_dir.mkdir()
        local_config = local_config_dir / "config.toml"
        local_config.write_text('active_model = "test"', encoding="utf-8")

        reset_harness_files_manager()
        init_harness_files_manager("user", "project")
        from vibe.core.config.harness_files import get_harness_files_manager

        mgr = get_harness_files_manager()
        assert mgr.config_file == VIBE_HOME.path / "config.toml"

    def test_falls_back_to_global_config_when_local_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        # Ensure no local config exists
        assert not (tmp_path / ".vibe" / "config.toml").exists()

        reset_harness_files_manager()
        init_harness_files_manager("user", "project")
        from vibe.core.config.harness_files import get_harness_files_manager

        mgr = get_harness_files_manager()
        assert mgr.config_file == VIBE_HOME.path / "config.toml"

    def test_respects_vibe_home_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert VIBE_HOME.path != tmp_path
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        assert VIBE_HOME.path == tmp_path

    def test_returns_none_when_no_sources(self) -> None:
        mgr = HarnessFilesManager(sources=())
        assert mgr.config_file is None

    def test_user_only_returns_global_config(self) -> None:
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.config_file == VIBE_HOME.path / "config.toml"


class TestSaveUpdates:
    @pytest.mark.asyncio
    async def test_set_field_replaces_top_level_list_on_both_orchestrators(
        self,
        config_dir: Path,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        config_file = config_dir / "config.toml"
        with config_file.open("wb") as f:
            tomli_w.dump({"installed_agents": ["lean", "other"]}, f)

        orch = await make_orchestrator()
        failures = await orch.set_field("/installed_agents", ["lean"])

        assert failures == []
        with config_file.open("rb") as f:
            assert tomllib.load(f)["installed_agents"] == ["lean"]

        await orch.reload()
        assert orch.config.installed_agents == ["lean"]


class TestSystemTrustStoreConfig:
    @pytest.mark.asyncio
    async def test_build_configures_ssl_context_from_toml(
        self,
        config_dir: Path,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        config_file = config_dir / "config.toml"
        with config_file.open("rb") as f:
            data = tomllib.load(f)
        data["enable_system_trust_store"] = True
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        with patch(
            "vibe.core.config.default_orchestrator.configure_ssl_context"
        ) as configure:
            orch = await make_orchestrator()

        assert orch.config.enable_system_trust_store is True
        configure.assert_called_once_with(enable_system_trust_store=True)

    @pytest.mark.asyncio
    async def test_build_configures_ssl_context_from_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        monkeypatch.setenv("VIBE_ENABLE_SYSTEM_TRUST_STORE", "true")

        with patch(
            "vibe.core.config.default_orchestrator.configure_ssl_context"
        ) as configure:
            orch = await make_orchestrator()

        assert orch.config.enable_system_trust_store is True
        configure.assert_called_once_with(enable_system_trust_store=True)

    @pytest.mark.asyncio
    async def test_build_clears_cached_ssl_context_when_setting_changes(
        self,
        config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)
        configure_ssl_context(enable_system_trust_store=False)
        build_ssl_context.cache_clear()

        try:
            config_file = config_dir / "config.toml"
            with config_file.open("rb") as f:
                data = tomllib.load(f)

            data["enable_system_trust_store"] = False
            with config_file.open("wb") as f:
                tomli_w.dump(data, f)

            default_ctx = MagicMock(spec=ssl.SSLContext)
            with patch(
                "vibe.core.utils.http.ssl.create_default_context",
                return_value=default_ctx,
            ):
                await make_orchestrator()
                assert build_ssl_context() is default_ctx

            data["enable_system_trust_store"] = True
            with config_file.open("wb") as f:
                tomli_w.dump(data, f)

            truststore_ctx = MagicMock(spec=ssl.SSLContext)
            with patch(
                "vibe.core.utils.http.truststore.SSLContext",
                return_value=truststore_ctx,
            ):
                await make_orchestrator()
                assert build_ssl_context() is truststore_ctx
        finally:
            configure_ssl_context(enable_system_trust_store=False)
            build_ssl_context.cache_clear()


class TestModelThinkingFieldUpdate:
    @pytest.mark.asyncio
    async def test_persists_thinking_to_toml(
        self,
        config_dir: Path,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        config_file = config_dir / "config.toml"
        data = {
            "active_model": "my-model",
            "models": [
                {"name": "my-model", "provider": "mistral", "alias": "my-model"}
            ],
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        orch = await make_orchestrator()
        await orch.set_field("/models/my-model/thinking", "high")

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        entry = next(m for m in result["models"] if m["alias"] == "my-model")
        assert entry["thinking"] == "high"

    @pytest.mark.asyncio
    async def test_persists_thinking_for_correct_model(
        self,
        config_dir: Path,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        config_file = config_dir / "config.toml"
        data = {
            "active_model": "model-b",
            "models": [
                {"name": "model-a", "provider": "mistral", "alias": "model-a"},
                {"name": "model-b", "provider": "mistral", "alias": "model-b"},
            ],
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        orch = await make_orchestrator()
        await orch.set_field("/models/model-b/thinking", "max")

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        model_a = next(m for m in result["models"] if m["alias"] == "model-a")
        model_b = next(m for m in result["models"] if m["alias"] == "model-b")
        assert model_a.get("thinking") is None
        assert model_b["thinking"] == "max"

    @pytest.mark.asyncio
    async def test_persists_thinking_from_model_mapping_as_legacy_list(
        self,
        config_dir: Path,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        config_file = config_dir / "config.toml"
        data = {
            "active_model": "my-model",
            "models": {"my-model": {"name": "my-model", "provider": "mistral"}},
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        orch = await make_orchestrator()
        await orch.set_field("/models/my-model/thinking", "high")

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["models"] == [
            {
                "name": "my-model",
                "provider": "mistral",
                "alias": "my-model",
                "thinking": "high",
            }
        ]

    @pytest.mark.asyncio
    async def test_sparse_default_model_update_preserves_effective_defaults(
        self,
        config_dir: Path,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        config_file = config_dir / "config.toml"
        data = {"active_model": "mistral-medium-3.5"}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        orch = await make_orchestrator()
        await orch.set_field("/models/mistral-medium-3.5/thinking", "low")

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["models"] == [{"alias": "mistral-medium-3.5", "thinking": "low"}]

        await orch.reload()

        model = orch.config.models["mistral-medium-3.5"]
        assert model.thinking == "low"
        assert model.supports_images is True


class TestMigrateLeavesFindInBashAllowlist:
    def test_keeps_find_in_config_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "applied_migrations": [BASH_READ_ONLY_MIGRATION],
            "tools": {"bash": {"allowlist": ["echo", "ls"]}},
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["bash"]["allowlist"] == ["echo", "find", "ls"]

    def test_noop_when_find_already_present(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "applied_migrations": [BASH_READ_ONLY_MIGRATION],
            "tools": {"bash": {"allowlist": ["echo", "find", "ls"]}},
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["bash"]["allowlist"] == ["echo", "find", "ls"]

    def test_noop_when_no_bash_tools_section(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {"active_model": "test"}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert "tools" not in result


class TestMigrateStripsBashAllowlistWildcardSuffix:
    def test_strips_trailing_wildcard_from_bash_allowlist_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "applied_migrations": [BASH_READ_ONLY_MIGRATION],
            "tools": {"bash": {"allowlist": ["git commit *", "npm install *", "echo"]}},
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["bash"]["allowlist"] == [
            "echo",
            "find",
            "git commit",
            "npm install",
        ]

    def test_dedupes_when_stripping_collides_with_existing_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "applied_migrations": [BASH_READ_ONLY_MIGRATION],
            "tools": {"bash": {"allowlist": ["git commit *", "git commit", "find"]}},
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["bash"]["allowlist"] == ["find", "git commit"]

    def test_noop_when_no_wildcard_suffix_present(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "applied_migrations": [BASH_READ_ONLY_MIGRATION],
            "tools": {"bash": {"allowlist": ["echo", "find", "ls"]}},
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["bash"]["allowlist"] == ["echo", "find", "ls"]


class TestMigrateBashReadOnlyDefaults:
    def test_merges_read_only_commands_into_existing_allowlist(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        from vibe.core.tools.builtins.bash import default_read_only_commands

        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {"tools": {"bash": {"allowlist": ["echo", "git commit"]}}}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        allowlist = result["tools"]["bash"]["allowlist"]
        assert "git commit" in allowlist
        for cmd in default_read_only_commands():
            assert cmd in allowlist
        assert BASH_READ_ONLY_MIGRATION in result["applied_migrations"]

    def test_does_not_readd_removed_command_after_migration(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "applied_migrations": [BASH_READ_ONLY_MIGRATION],
            "tools": {"bash": {"allowlist": ["echo", "find"]}},
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["bash"]["allowlist"] == ["echo", "find"]

    def test_noop_when_no_bash_allowlist(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {"active_model": "test"}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result == {"active_model": "test"}


class TestMigrateMistralVibeCliLatestDefaults:
    def test_updates_alias_temperature_and_thinking_for_default_model(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "devstral-2",
                    "temperature": 0.2,
                    "thinking": "off",
                }
            ]
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["models"][0]["alias"] == "mistral-medium-3.5"
        assert result["models"][0]["temperature"] == 1.0
        assert result["models"][0]["input_price"] == 1.5
        assert result["models"][0]["output_price"] == 7.5
        assert result["models"][0]["thinking"] == "high"

    def test_updates_active_model_when_devstral_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "active_model": "devstral-2",
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "devstral-2",
                }
            ],
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["active_model"] == "mistral-medium-3.5"

    def test_adds_temperature_and_thinking_when_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "devstral-2",
                }
            ]
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["models"][0]["alias"] == "mistral-medium-3.5"
        assert result["models"][0]["temperature"] == 1.0
        assert result["models"][0]["input_price"] == 1.5
        assert result["models"][0]["output_price"] == 7.5
        assert result["models"][0]["thinking"] == "high"

    def test_skips_model_with_customized_alias(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "my-custom-alias",
                    "temperature": 0.2,
                    "thinking": "off",
                }
            ]
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["models"][0]["alias"] == "my-custom-alias"
        assert result["models"][0]["temperature"] == 0.2
        assert result["models"][0]["thinking"] == "off"

    def test_does_not_touch_other_models(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "devstral-2",
                },
                {
                    "name": "other-model",
                    "provider": "mistral",
                    "alias": "devstral-2-clone",
                    "temperature": 0.5,
                    "thinking": "low",
                },
            ]
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        other = next(m for m in result["models"] if m["alias"] == "devstral-2-clone")
        assert other["temperature"] == 0.5
        assert other["thinking"] == "low"

    def test_noop_when_no_models_section(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {"theme": "dark"}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result == {"theme": "dark"}

    def test_idempotent_when_already_migrated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "active_model": "mistral-medium-3.5",
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "mistral-medium-3.5",
                    "temperature": 1.0,
                    "input_price": 1.5,
                    "output_price": 7.5,
                    "thinking": "high",
                }
            ],
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["active_model"] == "mistral-medium-3.5"
        assert result["models"][0]["alias"] == "mistral-medium-3.5"
        assert result["models"][0]["temperature"] == 1.0
        assert result["models"][0]["input_price"] == 1.5
        assert result["models"][0]["output_price"] == 7.5
        assert result["models"][0]["thinking"] == "high"

    def test_migrates_model_and_active_model_together(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "active_model": "devstral-2",
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "devstral-2",
                }
            ],
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["active_model"] == "mistral-medium-3.5"
        assert result["models"][0]["alias"] == "mistral-medium-3.5"
        assert result["models"][0]["temperature"] == 1.0
        assert result["models"][0]["input_price"] == 1.5
        assert result["models"][0]["output_price"] == 7.5
        assert result["models"][0]["thinking"] == "high"

    def test_backfills_supports_images_on_existing_mistral_medium_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "active_model": "mistral-medium-3.5",
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "mistral-medium-3.5",
                    "temperature": 1.0,
                    "input_price": 1.5,
                    "output_price": 7.5,
                    "thinking": "high",
                }
            ],
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["models"][0]["supports_images"] is True

    def test_preserves_explicit_supports_images_false(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "mistral-medium-3.5",
                    "supports_images": False,
                }
            ]
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["models"][0]["supports_images"] is False


class TestAutoCompactThresholdFallback:
    def test_model_without_explicit_threshold_inherits_global(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")
        cfg = make_config(
            auto_compact_threshold=42_000, models=[model], active_model="m"
        )
        assert cfg.get_active_model().auto_compact_threshold == 42_000

    def test_model_with_explicit_threshold_keeps_own_value(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(
            name="m", provider="p", alias="m", auto_compact_threshold=99_000
        )
        cfg = make_config(
            auto_compact_threshold=42_000, models=[model], active_model="m"
        )
        assert cfg.get_active_model().auto_compact_threshold == 99_000

    def test_default_global_threshold_used_when_nothing_set(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")
        cfg = make_config(models=[model], active_model="m")
        assert cfg.get_active_model().auto_compact_threshold == 200_000

    def test_changed_global_threshold_propagates_on_reload(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")

        cfg1 = make_config(
            auto_compact_threshold=50_000, models=[model], active_model="m"
        )
        assert cfg1.get_active_model().auto_compact_threshold == 50_000

        # Simulate config reload with a different global threshold
        cfg2 = make_config(
            auto_compact_threshold=75_000, models=[model], active_model="m"
        )
        assert cfg2.get_active_model().auto_compact_threshold == 75_000


class TestDefaultProviderConfig:
    def test_default_mistral_provider_is_mistral_backend(self) -> None:
        provider = _default_provider("mistral")

        assert provider.name == "mistral"
        assert provider.backend.value == "mistral"
        assert provider.browser_auth_base_url == DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL
        assert (
            provider.browser_auth_api_base_url
            == DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL
        )
        assert provider.supports_browser_sign_in is True

    def test_non_mistral_provider_does_not_inherit_browser_auth_defaults(self) -> None:
        provider = _default_provider("llamacpp")

        assert provider.browser_auth_base_url is None
        assert provider.browser_auth_api_base_url is None
        assert provider.supports_browser_sign_in is False


class TestMistralBrowserAuthConfig:
    def test_provider_browser_auth_urls_are_dumped_when_set(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        cfg = make_config()
        provider = cfg.get_active_provider()
        dumped = cfg.model_dump(mode="json")

        assert provider.browser_auth_base_url == DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL
        assert (
            provider.browser_auth_api_base_url
            == DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL
        )
        assert (
            dumped["providers"][0]["browser_auth_base_url"]
            == DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL
        )
        assert (
            dumped["providers"][0]["browser_auth_api_base_url"]
            == DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL
        )

    def test_legacy_explicit_mistral_provider_backfills_browser_auth_urls_without_changing_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        with config_file.open("wb") as f:
            tomli_w.dump(
                {
                    "active_model": "devstral-2",
                    "providers": [
                        {
                            "name": "mistral",
                            "api_base": "https://api.mistral.ai/v1",
                            "api_key_env_var": "MISTRAL_API_KEY",
                            "reasoning_field_name": "thoughts",
                        }
                    ],
                    "models": [
                        {
                            "name": "mistral-vibe-cli-latest",
                            "provider": "mistral",
                            "alias": "devstral-2",
                        }
                    ],
                },
                f,
            )

        reset_harness_files_manager()
        init_harness_files_manager("user")

        context = OnboardingContext.load()

        assert context.provider.name == "mistral"
        assert context.provider.backend.value == "generic"
        assert context.provider.reasoning_field_name == "thoughts"
        assert (
            context.provider.browser_auth_base_url
            == DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL
        )
        assert (
            context.provider.browser_auth_api_base_url
            == DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL
        )
        assert context.supports_browser_sign_in is True

    def test_legacy_explicit_mistral_provider_backfills_only_missing_browser_auth_url(
        self,
    ) -> None:
        provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            browser_auth_base_url="https://custom-console.example",
        )

        assert provider.backend.value == "generic"
        assert provider.browser_auth_base_url == "https://custom-console.example"
        assert (
            provider.browser_auth_api_base_url
            == DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL
        )
        assert provider.supports_browser_sign_in is True

    def test_legacy_mistral_provider_keeps_browser_sign_in_after_round_trip(
        self,
    ) -> None:
        provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
        )

        reloaded_provider = ProviderConfig.model_validate(
            provider.model_dump(mode="json")
        )

        assert reloaded_provider.supports_browser_sign_in is True

    def test_explicit_generic_mistral_provider_does_not_get_browser_auth_defaults(
        self,
    ) -> None:
        provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.GENERIC,
        )

        assert provider.backend.value == "generic"
        assert provider.browser_auth_base_url is None
        assert provider.browser_auth_api_base_url is None
        assert provider.supports_browser_sign_in is False

    def test_custom_provider_browser_auth_urls_round_trip(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        custom_provider = _custom_provider(
            browser_auth_base_url="https://custom.example/sign-in",
            browser_auth_api_base_url="https://custom.example/api",
            backend=Backend.MISTRAL,
        )
        cfg = make_config(
            active_model="custom-model",
            providers=[custom_provider],
            models=[_custom_model()],
        )

        dumped = cfg.model_dump(mode="json")
        reloaded_provider = ProviderConfig.model_validate(dumped["providers"][0])

        assert (
            reloaded_provider.browser_auth_base_url == "https://custom.example/sign-in"
        )
        assert (
            reloaded_provider.browser_auth_api_base_url == "https://custom.example/api"
        )
        assert reloaded_provider.supports_browser_sign_in is True

    def test_custom_mistral_provider_without_browser_auth_urls_is_not_capable(
        self,
    ) -> None:
        provider = _custom_provider(backend=Backend.MISTRAL)

        assert provider.browser_auth_base_url is None
        assert provider.browser_auth_api_base_url is None
        assert provider.supports_browser_sign_in is False

    def test_non_mistral_provider_with_browser_auth_urls_is_not_capable(self) -> None:
        provider = _custom_provider(
            browser_auth_base_url="https://custom.example/sign-in",
            browser_auth_api_base_url="https://custom.example/api",
        )

        assert provider.supports_browser_sign_in is False


class TestOnboardingContextResolution:
    def test_load_uses_explicit_overrides_when_harness_manager_is_uninitialized(
        self,
    ) -> None:
        reset_harness_files_manager()

        context = OnboardingContext.load(
            active_model="custom-model",
            providers=[_custom_provider_payload()],
            models=[_custom_model_payload()],
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_uses_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reset_harness_files_manager()
        monkeypatch.setenv("VIBE_ACTIVE_MODEL", "env-model")
        monkeypatch.setenv("VIBE_VIBE_BASE_URL", "https://env-vibe.example.com")
        monkeypatch.setenv(
            "VIBE_PROVIDERS",
            json.dumps([
                {
                    "name": "env-provider",
                    "api_base": "https://env.example/v1",
                    "api_key_env_var": "ENV_API_KEY",
                }
            ]),
        )
        monkeypatch.setenv(
            "VIBE_MODELS",
            json.dumps([
                {"name": "env-model", "provider": "env-provider", "alias": "env-model"}
            ]),
        )

        context = OnboardingContext.load()

        assert context.provider.name == "env-provider"
        assert context.provider.api_key_env_var == "ENV_API_KEY"
        assert context.vibe_base_url == "https://env-vibe.example.com"

    def test_load_prefers_explicit_overrides_over_toml_and_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        with config_file.open("wb") as file:
            tomli_w.dump(
                {
                    "active_model": "toml-model",
                    "providers": [
                        {
                            "name": "toml-provider",
                            "api_base": "https://toml.example/v1",
                            "api_key_env_var": "TOML_API_KEY",
                        }
                    ],
                    "models": [
                        {
                            "name": "toml-model",
                            "provider": "toml-provider",
                            "alias": "toml-model",
                        }
                    ],
                },
                file,
            )
        monkeypatch.setenv("VIBE_ACTIVE_MODEL", "env-model")
        monkeypatch.setenv(
            "VIBE_PROVIDERS",
            json.dumps([
                {
                    "name": "env-provider",
                    "api_base": "https://env.example/v1",
                    "api_key_env_var": "ENV_API_KEY",
                }
            ]),
        )
        monkeypatch.setenv(
            "VIBE_MODELS",
            json.dumps([
                {"name": "env-model", "provider": "env-provider", "alias": "env-model"}
            ]),
        )

        reset_harness_files_manager()
        init_harness_files_manager("user")

        context = OnboardingContext.load(
            active_model="custom-model",
            providers=[_custom_provider_payload()],
            models=[_custom_model_payload()],
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_accepts_typed_provider_and_model_overrides(self) -> None:
        context = OnboardingContext.load(
            active_model="custom-model",
            providers=[_custom_provider(api_key_env_var="CUSTOM_API_KEY")],
            models=[_custom_model()],
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_preserves_explicit_overrides_when_onboarding_toml_is_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        config_file.write_text("invalid = [", encoding="utf-8")

        reset_harness_files_manager()
        init_harness_files_manager("user")

        context = OnboardingContext.load(
            active_model="custom-model",
            providers=[_custom_provider_payload()],
            models=[_custom_model_payload()],
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_preserves_explicit_provider_and_model_overrides_when_toml_is_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        config_file.write_text("invalid = [", encoding="utf-8")

        reset_harness_files_manager()
        init_harness_files_manager("user")

        context = OnboardingContext.load(
            providers=[_custom_provider_payload()],
            models=[_custom_model_payload(alias="devstral-2")],
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_preserves_explicit_provider_override_when_toml_is_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        config_file.write_text("invalid = [", encoding="utf-8")

        reset_harness_files_manager()
        init_harness_files_manager("user")

        context = OnboardingContext.load(
            active_model="custom-model", providers=[_custom_provider_payload()]
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_preserves_explicit_overrides_when_onboarding_env_is_invalid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reset_harness_files_manager()
        monkeypatch.setenv("VIBE_PROVIDERS", "not-json")

        context = OnboardingContext.load(
            active_model="custom-model",
            providers=[_custom_provider_payload()],
            models=[_custom_model_payload()],
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_preserves_explicit_provider_override_when_onboarding_env_is_invalid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reset_harness_files_manager()
        monkeypatch.setenv("VIBE_MODELS", "not-json")

        context = OnboardingContext.load(
            active_model="custom-model", providers=[_custom_provider_payload()]
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_uses_valid_active_provider_when_unrelated_provider_is_malformed(
        self,
    ) -> None:
        context = OnboardingContext.load(
            active_model="custom-model",
            providers=[
                _custom_provider_payload(),
                {"name": "broken-provider", "backend": "mistral"},
            ],
            models=[_custom_model_payload()],
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_uses_valid_active_provider_when_unrelated_model_is_malformed(
        self,
    ) -> None:
        context = OnboardingContext.load(
            active_model="custom-model",
            providers=[_custom_provider_payload()],
            models=[
                _custom_model_payload(),
                {"name": "broken-model", "alias": "broken-model"},
            ],
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_uses_single_valid_provider_when_no_matching_model_exists(
        self,
    ) -> None:
        context = OnboardingContext.load(
            active_model="custom-model", providers=[_custom_provider_payload()]
        )

        assert context.provider.name == "custom-provider"
        assert context.provider.api_key_env_var == "CUSTOM_API_KEY"

    def test_load_preserves_browser_sign_in_for_valid_active_provider_with_unrelated_invalid_entry(
        self,
    ) -> None:
        context = OnboardingContext.load(
            active_model="custom-model",
            providers=[
                _custom_provider_payload(
                    browser_auth_base_url="https://custom.example/sign-in",
                    browser_auth_api_base_url="https://custom.example/api",
                    backend="mistral",
                ),
                {"name": "broken-provider", "backend": "mistral"},
            ],
            models=[_custom_model_payload()],
        )

        assert context.provider.name == "custom-provider"
        assert context.supports_browser_sign_in is True

    def test_load_falls_back_when_active_provider_is_invalid(self) -> None:
        context = OnboardingContext.load(
            active_model="custom-model",
            providers=[{"name": "custom-provider", "backend": "mistral"}],
            models=[_custom_model_payload()],
        )

        assert context.provider.name == "mistral"

    def test_load_falls_back_when_no_valid_provider_model_pair_exists(self) -> None:
        context = OnboardingContext.load(
            active_model="broken-model",
            providers=[{"name": "broken-provider", "backend": "mistral"}],
            models=[{"name": "broken-model", "alias": "broken-model"}],
        )

        assert context.provider.name == "mistral"

    def test_load_falls_back_when_onboarding_toml_is_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        config_file.write_text("invalid = [", encoding="utf-8")

        reset_harness_files_manager()
        init_harness_files_manager("user")

        context = OnboardingContext.load()

        assert context.provider.name == "mistral"

    def test_load_falls_back_when_onboarding_env_payload_is_invalid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reset_harness_files_manager()
        monkeypatch.setenv("VIBE_PROVIDERS", "not-json")

        context = OnboardingContext.load()

        assert context.provider.name == "mistral"


class TestCompactionModel:
    def test_get_compaction_model_returns_active_when_unset(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        cfg = make_config()
        assert cfg.get_compaction_model() == cfg.get_active_model()

    def test_get_compaction_model_returns_configured_model(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        compaction = ModelConfig(
            name="compact-model", provider="mistral", alias="compact"
        )
        cfg = make_config(compaction_model=compaction)
        assert cfg.get_compaction_model().name == "compact-model"

    def test_compaction_model_provider_must_match_active(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        from vibe.core.config import ProviderConfig

        compaction = ModelConfig(
            name="compact-model", provider="other", alias="compact"
        )
        providers = [
            ProviderConfig(
                name="mistral",
                api_base="https://api.mistral.ai/v1",
                api_key_env_var="MISTRAL_API_KEY",
            ),
            ProviderConfig(
                name="other",
                api_base="https://other.ai/v1",
                api_key_env_var="MISTRAL_API_KEY",
            ),
        ]
        with pytest.raises(ValueError, match="must share the same provider"):
            make_config(compaction_model=compaction, providers=providers)

    def test_compaction_model_provider_must_exist(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        compaction = ModelConfig(
            name="compact-model", provider="missing-provider", alias="compact"
        )
        with pytest.raises(
            ValueError,
            match="Provider 'missing-provider' for model 'compact-model' not found in configuration",
        ):
            make_config(compaction_model=compaction)

    def test_compaction_model_excluded_from_model_dump_when_none(self) -> None:
        cfg = build_test_vibe_config()
        dumped = cfg.model_dump(exclude_unset=True)
        assert "compaction_model" not in dumped


class TestActiveModelValidation:
    def test_unknown_active_model_falls_back_to_first(
        self, build_config: ConfigBuilder, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            cfg = build_config(active_model="does-not-exist")

        first_alias = next(iter(cfg.models))
        assert cfg.active_model == first_alias
        assert cfg.get_active_model().alias == first_alias
        assert (
            "Active model 'does-not-exist' is not in your configured models"
            in caplog.text
        )
        assert len(cfg.validation_warnings) == 1
        assert "does-not-exist" in cfg.validation_warnings[0]

    def test_known_active_model_is_not_overridden(
        self, build_config: ConfigBuilder
    ) -> None:
        models = [
            ModelConfig(name="model-a", provider="mistral", alias="a"),
            ModelConfig(name="model-b", provider="mistral", alias="b"),
        ]
        cfg = build_config(active_model="b", models=models)
        assert cfg.active_model == "b"
        assert cfg.validation_warnings == ()

    def test_no_models_raises(self, build_config: ConfigBuilder) -> None:
        with pytest.raises(ValueError, match="No models are configured"):
            build_config(models=[])

    def test_duplicate_model_alias_last_wins(self, build_config: ConfigBuilder) -> None:
        models = [
            ModelConfig(name="model-a", provider="mistral", alias="same"),
            ModelConfig(name="model-b", provider="mistral", alias="same"),
        ]
        cfg = build_config(models=models)
        assert list(cfg.models) == ["same"]
        assert cfg.models["same"].name == "model-b"


class TestGetMistralProvider:
    def test_returns_active_provider_when_it_is_mistral(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        cfg = make_config()
        provider = cfg.get_mistral_provider()
        active = cfg.get_active_provider()
        assert provider is active
        assert provider is not None
        assert provider.backend == Backend.MISTRAL

    def test_falls_back_to_first_mistral_provider_when_active_is_not_mistral(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        mistral_provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        )
        llamacpp_provider = ProviderConfig(
            name="llamacpp", api_base="http://127.0.0.1:8080/v1", api_key_env_var=""
        )
        llamacpp_model = ModelConfig(
            name="llama-local", provider="llamacpp", alias="llama-local"
        )
        cfg = make_config(
            providers=[llamacpp_provider, mistral_provider],
            models=[llamacpp_model],
            active_model="llama-local",
        )
        provider = cfg.get_mistral_provider()
        assert provider is mistral_provider

    def test_returns_none_when_no_mistral_provider(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        llamacpp_provider = ProviderConfig(
            name="llamacpp", api_base="http://127.0.0.1:8080/v1", api_key_env_var=""
        )
        llamacpp_model = ModelConfig(
            name="llama-local", provider="llamacpp", alias="llama-local"
        )
        cfg = make_config(
            providers=[llamacpp_provider],
            models=[llamacpp_model],
            active_model="llama-local",
        )
        assert cfg.get_mistral_provider() is None

    def test_falls_back_to_iterating_when_active_model_is_misconfigured(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        mistral_provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        )
        llamacpp_model = ModelConfig(
            name="llama-local", provider="llamacpp", alias="llama-local"
        )
        cfg = make_config(
            providers=[mistral_provider],
            models=[llamacpp_model],
            active_model="llama-local",
        )
        provider = cfg.get_mistral_provider()
        assert provider is mistral_provider


class TestIsActiveModelMistral:
    def test_returns_true_when_active_provider_is_mistral(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        cfg = make_config()
        assert cfg.is_active_model_mistral() is True

    def test_returns_false_when_active_provider_is_not_mistral(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        cfg = make_config(
            providers=[
                ProviderConfig(
                    name="llamacpp",
                    api_base="http://127.0.0.1:8080/v1",
                    api_key_env_var="",
                )
            ],
            models=[
                ModelConfig(
                    name="llama-local", provider="llamacpp", alias="llama-local"
                )
            ],
            active_model="llama-local",
        )
        assert cfg.is_active_model_mistral() is False

    def test_returns_false_when_active_model_resolution_fails(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        cfg = make_config(
            providers=[
                ProviderConfig(
                    name="mistral",
                    api_base="https://api.mistral.ai/v1",
                    api_key_env_var="MISTRAL_API_KEY",
                    backend=Backend.MISTRAL,
                )
            ],
            models=[
                ModelConfig(
                    name="llama-local", provider="llamacpp", alias="llama-local"
                )
            ],
            active_model="llama-local",
        )
        assert cfg.is_active_model_mistral() is False


class TestConnectorsByName:
    def test_maps_connectors_by_name(self, build_config: ConfigBuilder) -> None:
        cfg = build_config(
            connectors=[ConnectorConfig(name="github"), ConnectorConfig(name="linear")]
        )
        by_name = cfg.connectors_by_name()
        assert set(by_name) == {"github", "linear"}
        assert by_name["github"].name == "github"

    def test_empty_by_default(self, build_config: ConfigBuilder) -> None:
        assert build_config().connectors_by_name() == {}


class TestAddToolAllowlistPatterns:
    @pytest.mark.asyncio
    async def test_strips_bash_wildcard(
        self,
        config_dir: Path,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        orch = await make_orchestrator()
        payload = orch.config.build_tool_allowlist_update("bash", ["ls *"])
        assert payload is not None
        await orch.set_field(
            "/tools/bash/allowlist", payload["tools"]["bash"]["allowlist"]
        )

        with (config_dir / "config.toml").open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["bash"]["allowlist"] == ["ls"]

    @pytest.mark.asyncio
    async def test_merges_and_sorts_with_existing(
        self,
        config_dir: Path,
        make_orchestrator: Callable[
            [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
        ],
    ) -> None:
        config_file = config_dir / "config.toml"
        with config_file.open("rb") as f:
            data = tomllib.load(f)
        data.setdefault("tools", {})["edit"] = {"allowlist": ["c"]}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        orch = await make_orchestrator()
        payload = orch.config.build_tool_allowlist_update("edit", ["b", "a"])
        assert payload is not None
        await orch.set_field(
            "/tools/edit/allowlist", payload["tools"]["edit"]["allowlist"]
        )

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["edit"]["allowlist"] == ["a", "b", "c"]

    def test_noop_when_nothing_new(
        self, config_dir: Path, build_config: ConfigBuilder
    ) -> None:
        cfg = build_config(tools={"edit": {"allowlist": ["a"]}})

        assert cfg.build_tool_allowlist_update("edit", ["a"]) is None
        with (config_dir / "config.toml").open("rb") as f:
            persisted = tomllib.load(f)
        assert "edit" not in persisted.get("tools", {})

    def test_keeps_in_memory_config_current(self, build_config: ConfigBuilder) -> None:
        cfg = build_config(tools={"edit": {"allowlist": ["a"]}})

        first = cfg.build_tool_allowlist_update("edit", ["b"])
        assert first == {"tools": {"edit": {"allowlist": ["a", "b"]}}}
        assert cfg.tools["edit"]["allowlist"] == ["a", "b"]

        second = cfg.build_tool_allowlist_update("edit", ["c"])
        assert second == {"tools": {"edit": {"allowlist": ["a", "b", "c"]}}}


class TestMigrateRenamedTools:
    def test_renames_read_and_search_replace_keys(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "tools": {
                "read": {
                    "permission": "always",
                    "allowlist": ["src/**"],
                    "max_read_bytes": 64000,
                },
                "search_replace": {
                    "allowlist": ["src/**"],
                    "max_content_size": 100000,
                    "create_backup": True,
                },
            }
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        tools = result["tools"]
        assert "read" not in tools
        assert "search_replace" not in tools
        assert tools["read_file"] == {
            "permission": "always",
            "allowlist": ["src/**"],
            "max_read_bytes": 64000,
        }
        # Common options carry over; edit-incompatible options are dropped.
        assert tools["edit"] == {"allowlist": ["src/**"]}

    def test_prefers_existing_new_key_and_drops_legacy(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {
            "tools": {
                "read": {"permission": "ask"},
                "read_file": {"permission": "always"},
            }
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert "read" not in result["tools"]
        assert result["tools"]["read_file"] == {"permission": "always"}

    def test_renames_entries_in_enabled_and_disabled_lists(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {"enabled_tools": ["read", "grep"], "disabled_tools": ["search_replace"]}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["enabled_tools"] == ["read_file", "grep"]
        assert result["disabled_tools"] == ["edit"]

    def test_noop_when_no_legacy_tool_names(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_migration: Callable[[], None],
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {"tools": {"read_file": {"permission": "always"}}}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        run_migration()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"] == {"read_file": {"permission": "always"}}
