from __future__ import annotations

import json
from pathlib import Path
import tomllib
from typing import Literal, TypedDict, Unpack

import pytest
import tomli_w

from tests.conftest import build_test_vibe_config
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.config._settings import (
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
    DEFAULT_PROVIDERS,
)
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.paths import VIBE_HOME
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.types import Backend
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
    def test_merges_nested_tool_updates_without_materializing_defaults(
        self, config_dir: Path
    ) -> None:
        config_file = config_dir / "config.toml"
        data = {"tools": {"bash": {"default_timeout": 600}}}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        VibeConfig.save_updates({"tools": {"bash": {"permission": "always"}}})

        with config_file.open("rb") as f:
            result = tomllib.load(f)

        assert result == {
            "tools": {"bash": {"default_timeout": 600, "permission": "always"}}
        }

    def test_replaces_lists_instead_of_unioning_them(self, config_dir: Path) -> None:
        config_file = config_dir / "config.toml"
        data = {"installed_agents": ["lean", "other"]}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        VibeConfig.save_updates({"installed_agents": ["lean"]})

        with config_file.open("rb") as f:
            result = tomllib.load(f)

        assert result["installed_agents"] == ["lean"]

    def test_prunes_nested_none_values_before_writing(self, config_dir: Path) -> None:
        config_file = config_dir / "config.toml"
        data = {"tools": {"bash": {"default_timeout": 600, "permission": "always"}}}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        VibeConfig.save_updates({"tools": {"bash": {"permission": None}}})

        with config_file.open("rb") as f:
            result = tomllib.load(f)

        assert result == {"tools": {"bash": {"default_timeout": 600}}}


class TestSetThinking:
    def test_persists_thinking_to_toml(self, config_dir: Path) -> None:
        config_file = config_dir / "config.toml"
        data = {
            "active_model": "my-model",
            "models": [
                {"name": "my-model", "provider": "mistral", "alias": "my-model"}
            ],
        }
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        cfg = VibeConfig.load()
        cfg.set_thinking("high")

        reloaded = VibeConfig.load()
        assert reloaded.get_active_model().thinking == "high"
        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["models"][0]["thinking"] == "high"

    def test_persists_thinking_for_correct_model(self, config_dir: Path) -> None:
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

        cfg = VibeConfig.load()
        cfg.set_thinking("max")

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["models"][0].get("thinking") is None
        assert result["models"][1]["thinking"] == "max"


class TestMigrateLeavesFindInBashAllowlist:
    def test_keeps_find_in_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {"tools": {"bash": {"allowlist": ["echo", "ls"]}}}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        VibeConfig._migrate()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["bash"]["allowlist"] == ["echo", "find", "ls"]

    def test_noop_when_find_already_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {"tools": {"bash": {"allowlist": ["echo", "find", "ls"]}}}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        VibeConfig._migrate()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert result["tools"]["bash"]["allowlist"] == ["echo", "find", "ls"]

    def test_noop_when_no_bash_tools_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        config_file = tmp_path / "config.toml"
        data = {"active_model": "test"}
        with config_file.open("wb") as f:
            tomli_w.dump(data, f)

        reset_harness_files_manager()
        init_harness_files_manager("user")
        VibeConfig._migrate()

        with config_file.open("rb") as f:
            result = tomllib.load(f)
        assert "tools" not in result


class TestAutoCompactThresholdFallback:
    def test_model_without_explicit_threshold_inherits_global(self) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")
        cfg = build_test_vibe_config(
            auto_compact_threshold=42_000, models=[model], active_model="m"
        )
        assert cfg.get_active_model().auto_compact_threshold == 42_000

    def test_model_with_explicit_threshold_keeps_own_value(self) -> None:
        model = ModelConfig(
            name="m", provider="p", alias="m", auto_compact_threshold=99_000
        )
        cfg = build_test_vibe_config(
            auto_compact_threshold=42_000, models=[model], active_model="m"
        )
        assert cfg.get_active_model().auto_compact_threshold == 99_000

    def test_default_global_threshold_used_when_nothing_set(self) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")
        cfg = build_test_vibe_config(models=[model], active_model="m")
        assert cfg.get_active_model().auto_compact_threshold == 200_000

    def test_changed_global_threshold_propagates_on_reload(self) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")

        cfg1 = build_test_vibe_config(
            auto_compact_threshold=50_000, models=[model], active_model="m"
        )
        assert cfg1.get_active_model().auto_compact_threshold == 50_000

        # Simulate config reload with a different global threshold
        cfg2 = build_test_vibe_config(
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
    def test_provider_browser_auth_urls_are_dumped_when_set(self) -> None:
        cfg = build_test_vibe_config()
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

    def test_custom_provider_browser_auth_urls_round_trip(self) -> None:
        custom_provider = _custom_provider(
            browser_auth_base_url="https://custom.example/sign-in",
            browser_auth_api_base_url="https://custom.example/api",
            backend=Backend.MISTRAL,
        )
        cfg = build_test_vibe_config(
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
    def test_get_compaction_model_returns_active_when_unset(self) -> None:
        cfg = build_test_vibe_config()
        assert cfg.get_compaction_model() == cfg.get_active_model()

    def test_get_compaction_model_returns_configured_model(self) -> None:
        compaction = ModelConfig(
            name="compact-model", provider="mistral", alias="compact"
        )
        cfg = build_test_vibe_config(compaction_model=compaction)
        assert cfg.get_compaction_model().name == "compact-model"

    def test_compaction_model_provider_must_match_active(self) -> None:
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
            build_test_vibe_config(compaction_model=compaction, providers=providers)

    def test_compaction_model_provider_must_exist(self) -> None:
        compaction = ModelConfig(
            name="compact-model", provider="missing-provider", alias="compact"
        )
        with pytest.raises(
            ValueError,
            match="Provider 'missing-provider' for model 'compact-model' not found in configuration",
        ):
            build_test_vibe_config(compaction_model=compaction)

    def test_compaction_model_excluded_from_model_dump_when_none(self) -> None:
        cfg = build_test_vibe_config()
        dumped = cfg.model_dump()
        assert "compaction_model" not in dumped


class TestGetMistralProvider:
    def test_returns_active_provider_when_it_is_mistral(self) -> None:
        cfg = build_test_vibe_config()
        provider = cfg.get_mistral_provider()
        active = cfg.get_active_provider()
        assert provider is active
        assert provider is not None
        assert provider.backend == Backend.MISTRAL

    def test_falls_back_to_first_mistral_provider_when_active_is_not_mistral(
        self,
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
        cfg = build_test_vibe_config(
            providers=[llamacpp_provider, mistral_provider],
            models=[llamacpp_model],
            active_model="llama-local",
        )
        provider = cfg.get_mistral_provider()
        assert provider is mistral_provider

    def test_returns_none_when_no_mistral_provider(self) -> None:
        llamacpp_provider = ProviderConfig(
            name="llamacpp", api_base="http://127.0.0.1:8080/v1", api_key_env_var=""
        )
        llamacpp_model = ModelConfig(
            name="llama-local", provider="llamacpp", alias="llama-local"
        )
        cfg = build_test_vibe_config(
            providers=[llamacpp_provider],
            models=[llamacpp_model],
            active_model="llama-local",
        )
        assert cfg.get_mistral_provider() is None

    def test_falls_back_to_iterating_when_active_model_is_misconfigured(self) -> None:
        mistral_provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        )
        llamacpp_model = ModelConfig(
            name="llama-local", provider="llamacpp", alias="llama-local"
        )
        cfg = build_test_vibe_config(
            providers=[mistral_provider],
            models=[llamacpp_model],
            active_model="llama-local",
        )
        provider = cfg.get_mistral_provider()
        assert provider is mistral_provider


class TestIsActiveModelMistral:
    def test_returns_true_when_active_provider_is_mistral(self) -> None:
        cfg = build_test_vibe_config()
        assert cfg.is_active_model_mistral() is True

    def test_returns_false_when_active_provider_is_not_mistral(self) -> None:
        cfg = build_test_vibe_config(
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

    def test_returns_false_when_active_model_resolution_fails(self) -> None:
        cfg = build_test_vibe_config(
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
