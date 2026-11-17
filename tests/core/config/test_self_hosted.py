from __future__ import annotations

import logging

import pytest

from tests.conftest import ConfigBuilder
from vibe.core.config import (
    ModelConfig,
    ProviderConfig,
    TranscribeProviderConfig,
    TTSProviderConfig,
    VibeConfigSchema,
    build_default_orchestrator,
)
from vibe.core.config._self_hosted import (
    crash_reports_allowed,
    experiments_allowed,
    log_self_hosted_services,
    self_hosted_host,
)
from vibe.core.types import Backend


@pytest.mark.parametrize(
    ("api_base", "host"),
    [
        ("https://api.mistral.ai/v1", None),
        ("https://api.eu.mistral.ai/v1", None),
        ("https://api.customer.mistral.ai/v1", None),
        ("https://API.Mistral.AI/v1", None),
        ("https://api.asml.example/v1", "api.asml.example"),
        ("http://10.0.0.5:8080/v1", "10.0.0.5"),
        ("https://api.mistral.ai.example.com/v1", "api.mistral.ai.example.com"),
        ("https://notmistral.ai/v1", "notmistral.ai"),
        ("", ""),
        ("not a url", "not a url"),
    ],
)
def test_the_api_base_host_decides_self_hosted(
    api_base: str, host: str | None, build_config: ConfigBuilder
) -> None:
    config = build_config(providers=[_mistral(api_base)])

    assert self_hosted_host(config) == host


def _mistral(api_base: str) -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base=api_base,
        api_key_env_var="MISTRAL_API_KEY",
        backend=Backend.MISTRAL,
    )


@pytest.mark.parametrize(
    ("api_base", "allowed"),
    [
        ("https://api.mistral.ai/v1", True),
        ("https://api.customer.mistral.ai/v1", True),
        ("https://api.asml.example/v1", False),
    ],
)
def test_telemetry_on_allows_experiments_and_crash_reports_off_self_hosted(
    api_base: str, allowed: bool, build_config: ConfigBuilder
) -> None:
    config = build_config(
        enable_telemetry=True,
        experiments={"enable": True},
        providers=[_mistral(api_base)],
    )

    assert experiments_allowed(config) is allowed
    assert crash_reports_allowed(config) is allowed


def test_telemetry_off_allows_neither(build_config: ConfigBuilder) -> None:
    config = build_config(enable_telemetry=False)

    assert experiments_allowed(config) is False
    assert crash_reports_allowed(config) is False


def test_without_a_mistral_provider_only_telemetry_decides(
    build_config: ConfigBuilder,
) -> None:
    config = build_config(
        enable_telemetry=True,
        providers=[ProviderConfig(name="local", api_base="http://127.0.0.1:8080/v1")],
        models={"m": ModelConfig(name="m", provider="local", alias="m")},
        active_model="m",
    )

    assert crash_reports_allowed(config) is True


@pytest.mark.parametrize(
    ("api_base", "transcription", "speech"),
    [
        ("https://api.mistral.ai/v1", "wss://api.mistral.ai", "https://api.mistral.ai"),
        (
            "https://api.eu.mistral.ai/v1",
            "wss://api.mistral.ai",
            "https://api.mistral.ai",
        ),
        (
            "https://api.customer.mistral.ai/v1",
            "wss://api.customer.mistral.ai",
            "https://api.customer.mistral.ai",
        ),
        (
            "https://api.asml.example/v1",
            "wss://api.asml.example",
            "https://api.asml.example",
        ),
        ("http://10.0.0.5:8080/v1", "ws://10.0.0.5:8080", "http://10.0.0.5:8080"),
    ],
)
def test_default_voice_follows_the_mistral_provider(
    api_base: str, transcription: str, speech: str, build_config: ConfigBuilder
) -> None:
    config = build_config(providers=[_mistral(api_base)])

    assert _voice_servers(config) == (transcription, speech)
    # The stored config keeps the built-in addresses: only the lookup follows.
    assert [p.api_base for p in config.transcribe_providers] == ["wss://api.mistral.ai"]
    assert [p.api_base for p in config.tts_providers] == ["https://api.mistral.ai"]


@pytest.mark.asyncio
async def test_a_configured_voice_address_is_kept() -> None:
    orchestrator = await build_default_orchestrator({
        "providers": [_mistral("https://api.asml.example/v1").model_dump()],
        "transcribe_providers": [
            TranscribeProviderConfig(
                name="mistral",
                api_base="wss://voice.asml.example",
                api_key_env_var="MISTRAL_API_KEY",
            ).model_dump()
        ],
    })

    assert _voice_servers(orchestrator.config) == (
        "wss://voice.asml.example",
        "https://api.asml.example",
    )


@pytest.mark.asyncio
async def test_built_in_voice_entries_in_a_layer_still_follow_the_deployment() -> None:
    # Configs from before July 2026 hold the built-in entries Vibe wrote there,
    # and a managed config may add a provider of its own beside them.
    orchestrator = await build_default_orchestrator({
        "providers": [_mistral("https://api.asml.example/v1").model_dump()],
        "transcribe_providers": [
            TranscribeProviderConfig(
                name="mistral",
                api_base="wss://api.mistral.ai",
                api_key_env_var="MISTRAL_API_KEY",
            ).model_dump()
        ],
        "tts_providers": [
            TTSProviderConfig(name="other", api_base="https://tts.example").model_dump()
        ],
    })

    assert _voice_servers(orchestrator.config) == (
        "wss://api.asml.example",
        "https://api.asml.example",
    )


def test_default_voice_uses_the_mistral_provider_key(
    build_config: ConfigBuilder,
) -> None:
    mistral = _mistral("https://api.asml.example/v1").model_copy(
        update={"api_key_env_var": "ASML_API_KEY"}
    )
    config = build_config(providers=[mistral])

    transcription = config.get_transcribe_provider_for_model(
        config.get_active_transcribe_model()
    )
    speech = config.get_tts_provider_for_model(config.get_active_tts_model())

    assert transcription.api_key_env_var == "ASML_API_KEY"
    assert speech.api_key_env_var == "ASML_API_KEY"


def _voice_servers(config: VibeConfigSchema) -> tuple[str, str]:
    transcription = config.get_transcribe_provider_for_model(
        config.get_active_transcribe_model()
    )
    speech = config.get_tts_provider_for_model(config.get_active_tts_model())
    return transcription.api_base, speech.api_base


def test_a_self_hosted_deployment_says_so_in_the_log(
    build_config: ConfigBuilder, caplog: pytest.LogCaptureFixture
) -> None:
    config = build_config(providers=[_mistral("https://api.asml.example/v1")])

    with caplog.at_level(logging.WARNING, logger="vibe"):
        log_self_hosted_services(config)

    assert (
        "Self-hosted deployment (api.asml.example): "
        "experiments and crash reports are off"
    ) in caplog.text


def test_saas_logs_nothing(
    build_config: ConfigBuilder, caplog: pytest.LogCaptureFixture
) -> None:
    config = build_config(providers=[_mistral("https://api.mistral.ai/v1")])

    with caplog.at_level(logging.INFO, logger="vibe"):
        log_self_hosted_services(config)

    assert "Self-hosted" not in caplog.text
