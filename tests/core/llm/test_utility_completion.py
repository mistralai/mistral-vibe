from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.llm import utility_completion
from vibe.core.llm.utility_completion import (
    run_utility_completion,
    select_utility_model,
)


def _anthropic_config():
    return build_test_vibe_config(
        providers=[
            ProviderConfig(
                name="mistral",
                api_base="https://api.mistral.ai/v1",
                api_key_env_var="MISTRAL_API_KEY",
            ),
            ProviderConfig(
                name="anthropic",
                api_base="https://api.anthropic.com",
                api_key_env_var="ANTHROPIC_API_KEY",
            ),
        ],
        models=[
            ModelConfig(name="claude-test", provider="anthropic", alias="anthropic")
        ],
        active_model="anthropic",
    )


class TestSelectUtilityModel:
    def test_picks_small_mistral_when_active_provider_mistral(self) -> None:
        model, provider = select_utility_model(build_test_vibe_config())

        assert model.alias == "mistral-small"
        assert provider.name == "mistral"

    def test_uses_active_model_when_active_provider_differs(self) -> None:
        # The session talks to Anthropic: utility calls stay on that provider and
        # never leak to Mistral, regardless of which keys happen to be present.
        model, provider = select_utility_model(_anthropic_config())

        assert provider.name == "anthropic"
        assert model.alias == "anthropic"

    def test_uses_active_model_when_fast_model_not_allowed(self) -> None:
        config = build_test_vibe_config(
            providers=[
                ProviderConfig(
                    name="mistral",
                    api_base="https://api.mistral.ai/v1",
                    api_key_env_var="MISTRAL_API_KEY",
                )
            ],
            models=[
                ModelConfig(
                    name="mistral-vibe-cli-latest",
                    provider="mistral",
                    alias="devstral-latest",
                )
            ],
            active_model="devstral-latest",
            allowed_models=["devstral-latest"],
        )

        model, provider = select_utility_model(config)

        assert model.alias == "devstral-latest"
        assert provider.name == "mistral"


class TestRunUtilityCompletion:
    @pytest.mark.asyncio
    async def test_returns_message_content(self, monkeypatch) -> None:
        config = build_test_vibe_config()
        monkeypatch.setattr(
            utility_completion,
            "create_backend",
            lambda **_: FakeBackend([mock_llm_chunk(content="the answer")]),
        )

        content = await run_utility_completion(
            config=config,
            system_prompt="system",
            user_content="user",
            max_tokens=24,
            request_timeout_seconds=1.0,
            retry_budget_seconds=0.0,
        )

        assert content == "the answer"

    @pytest.mark.asyncio
    async def test_forwards_budgets_to_the_backend(self, monkeypatch) -> None:
        config = build_test_vibe_config()
        captured: dict = {}

        def fake_create_backend(**kwargs):
            captured.update(kwargs)
            return FakeBackend([mock_llm_chunk(content="ok")])

        monkeypatch.setattr(utility_completion, "create_backend", fake_create_backend)

        await run_utility_completion(
            config=config,
            system_prompt="system",
            user_content="user",
            max_tokens=24,
            request_timeout_seconds=1.5,
            retry_budget_seconds=7.0,
        )

        assert captured["timeout"] == 1.5
        assert captured["retry_max_elapsed_time"] == 7.0

    @pytest.mark.asyncio
    async def test_propagates_backend_errors(self, monkeypatch) -> None:
        config = build_test_vibe_config()
        monkeypatch.setattr(
            utility_completion,
            "create_backend",
            lambda **_: FakeBackend(exception_to_raise=RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            await run_utility_completion(
                config=config,
                system_prompt="system",
                user_content="user",
                max_tokens=24,
                request_timeout_seconds=1.0,
                retry_budget_seconds=0.0,
            )

    @pytest.mark.asyncio
    async def test_skip_if_no_key_returns_none_without_touching_backend(
        self, monkeypatch
    ) -> None:
        config = build_test_vibe_config()
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

        def fail_create_backend(**_):
            raise AssertionError("backend must not be built when the key is missing")

        monkeypatch.setattr(utility_completion, "create_backend", fail_create_backend)

        content = await run_utility_completion(
            config=config,
            system_prompt="system",
            user_content="user",
            max_tokens=24,
            request_timeout_seconds=1.0,
            retry_budget_seconds=0.0,
            skip_if_no_key=True,
        )

        assert content is None

    @pytest.mark.asyncio
    async def test_skip_if_no_key_runs_for_keyless_local_provider(
        self, monkeypatch
    ) -> None:
        # A local provider declares no api_key_env_var; it needs no key, so the
        # skip must not fire even though resolve_api_key("") is always None.
        config = build_test_vibe_config(
            providers=[
                ProviderConfig(
                    name="local",
                    api_base="http://localhost:8080/v1",
                    api_key_env_var="",
                )
            ],
            models=[ModelConfig(name="local-model", provider="local", alias="local")],
            active_model="local",
        )
        monkeypatch.setattr(
            utility_completion,
            "create_backend",
            lambda **_: FakeBackend([mock_llm_chunk(content="named")]),
        )

        content = await run_utility_completion(
            config=config,
            system_prompt="system",
            user_content="user",
            max_tokens=24,
            request_timeout_seconds=1.0,
            retry_budget_seconds=0.0,
            skip_if_no_key=True,
        )

        assert content == "named"

    @pytest.mark.asyncio
    async def test_skip_if_no_key_runs_when_key_present(self, monkeypatch) -> None:
        config = build_test_vibe_config()
        monkeypatch.setenv("MISTRAL_API_KEY", "present")
        monkeypatch.setattr(
            utility_completion,
            "create_backend",
            lambda **_: FakeBackend([mock_llm_chunk(content="named")]),
        )

        content = await run_utility_completion(
            config=config,
            system_prompt="system",
            user_content="user",
            max_tokens=24,
            request_timeout_seconds=1.0,
            retry_budget_seconds=0.0,
            skip_if_no_key=True,
        )

        assert content == "named"
