from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from vibe.app_server._auth_context import OnboardingContextReader
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.setup.auth.onboarding_context import (
    CONFIG_ERROR_MODEL_INVALID,
    CUSTOM_CONSOLE_URL_ENV_VAR,
)


def _reader() -> OnboardingContextReader:
    # org_config=None: a reader on local config only, like a test process that
    # never fetched managed config.
    return OnboardingContextReader(org_config=None)


@pytest.mark.asyncio
async def test_reader_resolves_the_active_provider_through_the_config_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression (PR #67665): a config that only adds a custom provider with
    # custom models, while the active model stays on the default mistral
    # provider, must resolve to mistral. The orchestrator's layer merge keeps
    # the default models; the old raw-TOML snapshot replaced them and fell
    # through to the first configured model's provider, marking auth as a
    # custom provider.
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    config_file = tmp_path / "config.toml"
    with config_file.open("wb") as file:
        tomli_w.dump(
            {
                "providers": [
                    {
                        "name": "acme-llm",
                        "api_base": "https://custom.example/v1",
                        "api_key_env_var": "CUSTOM_API_KEY",
                    }
                ],
                "models": [
                    {
                        "name": "custom-model",
                        "provider": "acme-llm",
                        "alias": "custom-model",
                    }
                ],
            },
            file,
        )

    reset_harness_files_manager()
    init_harness_files_manager("user")

    context = await _reader().load()

    assert context.provider.name == "mistral"
    assert context.provider.api_key_env_var == "MISTRAL_API_KEY"
    assert context.config_error is None


@pytest.mark.asyncio
async def test_reader_reads_the_pinned_console_url_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    monkeypatch.setenv(CUSTOM_CONSOLE_URL_ENV_VAR, "https://console.acme.internal")

    reset_harness_files_manager()
    init_harness_files_manager("user")

    context = await _reader().load()

    assert context.pinned_console_url == "https://console.acme.internal"


@pytest.mark.asyncio
async def test_reader_reports_a_reason_when_the_config_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text("invalid = [", encoding="utf-8")

    reset_harness_files_manager()
    init_harness_files_manager("user")

    context = await _reader().load()

    assert context.provider.name == "mistral"
    assert context.config_error == CONFIG_ERROR_MODEL_INVALID


@pytest.mark.asyncio
async def test_reader_rebuilds_when_the_config_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    config_file = tmp_path / "config.toml"

    reset_harness_files_manager()
    init_harness_files_manager("user")
    reader = _reader()

    context = await reader.load()
    assert context.vibe_base_url == "https://chat.mistral.ai"

    config_file.write_text(
        'vibe_base_url = "https://relay.example.com"\n', encoding="utf-8"
    )

    context = await reader.load()
    assert context.vibe_base_url == "https://relay.example.com"
