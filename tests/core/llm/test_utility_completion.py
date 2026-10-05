from __future__ import annotations

import asyncio

import pytest

from tests.conftest import build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.config import ModelConfig, ProviderConfig, UtilityFeature
from vibe.core.llm import utility_completion
from vibe.core.llm.model_probe import MODEL_AVAILABILITY
from vibe.core.llm.utility_completion import (
    FAST_MODEL_CANDIDATES,
    _is_public_mistral_api,
    ensure_utility_models_probed,
    is_fast_utility_model,
    run_utility_completion,
    select_utility_model,
)
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext
from vibe.core.types import Backend


def _anthropic_config():
    return build_test_vibe_config(
        providers=[
            ProviderConfig(
                name="mistral",
                api_base="https://api.mistral.ai/v1",
                api_key_env_var="MISTRAL_API_KEY",
                backend=Backend.MISTRAL,
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


def _anthropic_only_config():
    # A session on Anthropic with no Mistral provider configured at all.
    return build_test_vibe_config(
        providers=[
            ProviderConfig(
                name="anthropic",
                api_base="https://api.anthropic.com",
                api_key_env_var="ANTHROPIC_API_KEY",
            )
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
        assert is_fast_utility_model(build_test_vibe_config())

    def test_prefers_fast_mistral_across_providers_when_available(self) -> None:
        # The session talks to Anthropic, but a Mistral provider is configured and
        # its key resolves: the cheap fast model runs the background nicety rather
        # than the expensive coding model.
        config = _anthropic_config()

        model, provider = select_utility_model(config)

        assert model.alias == "mistral-small"
        assert provider.name == "mistral"
        assert is_fast_utility_model(config)

    def test_falls_back_to_active_when_mistral_key_missing(self, monkeypatch) -> None:
        # Without a resolvable Mistral key the cross-provider route is unusable, so
        # the utility call stays on the session's active model.
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        config = _anthropic_config()

        model, provider = select_utility_model(config)

        assert provider.name == "anthropic"
        assert model.alias == "anthropic"
        assert not is_fast_utility_model(config)

    def test_falls_back_to_active_when_no_mistral_provider(self) -> None:
        config = _anthropic_only_config()

        model, provider = select_utility_model(config)

        assert provider.name == "anthropic"
        assert model.alias == "anthropic"
        assert not is_fast_utility_model(config)

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
            allowed_models=["mistral-vibe-cli-latest"],
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

    @pytest.mark.asyncio
    async def test_call_type_and_launch_context_ride_the_request_metadata(
        self, monkeypatch
    ) -> None:
        backend = FakeBackend([mock_llm_chunk(content="ok")])
        monkeypatch.setattr(utility_completion, "create_backend", lambda **_: backend)
        config = build_test_vibe_config()

        await run_utility_completion(
            config=config,
            system_prompt="system",
            user_content="user",
            max_tokens=24,
            request_timeout_seconds=1.0,
            retry_budget_seconds=0.0,
            call_type="worktree_title",
            launch_context=LaunchContext(
                agent_entrypoint="cli",
                agent_version="1.0.0",
                client_name="vibe_cli",
                client_version="1.0.0",
            ),
            session_id="session-123",
        )

        metadata = backend._requests_metadata[0]
        assert metadata is not None
        assert metadata["call_type"] == "worktree_title"
        assert metadata["agent_entrypoint"] == "cli"
        assert metadata["client_name"] == "vibe_cli"
        assert metadata["session_id"] == "session-123"

    @pytest.mark.asyncio
    async def test_telemetry_client_emits_one_request_sent_event(
        self, monkeypatch
    ) -> None:
        class RecordingTelemetry(TelemetryClient):
            def __init__(self) -> None:
                super().__init__(config_getter=build_test_vibe_config)
                self.request_events: list[dict] = []

            def send_request_sent(self, **kwargs) -> None:
                self.request_events.append(kwargs)

        backend = FakeBackend([mock_llm_chunk(content="ok")])
        monkeypatch.setattr(utility_completion, "create_backend", lambda **_: backend)
        telemetry = RecordingTelemetry()
        config = build_test_vibe_config()

        await run_utility_completion(
            config=config,
            system_prompt="system prompt",
            user_content="user prompt",
            max_tokens=24,
            request_timeout_seconds=1.0,
            retry_budget_seconds=0.0,
            call_type="title_generation",
            telemetry=telemetry,
        )

        (event,) = telemetry.request_events
        assert event["call_type"] == "title_generation"
        assert event["model"] == "mistral-small"
        assert event["nb_context_chars"] == len("system prompt") + len("user prompt")
        assert event["nb_context_messages"] == 2
        assert event["nb_prompt_chars"] == len("user prompt")

    @pytest.mark.asyncio
    async def test_without_a_telemetry_client_no_event_is_emitted(
        self, monkeypatch
    ) -> None:
        # Worktree naming runs before a session exists: no telemetry client, so
        # the call rides the request metadata alone.
        backend = FakeBackend([mock_llm_chunk(content="ok")])
        monkeypatch.setattr(utility_completion, "create_backend", lambda **_: backend)
        config = build_test_vibe_config()

        await run_utility_completion(
            config=config,
            system_prompt="system",
            user_content="user",
            max_tokens=24,
            request_timeout_seconds=1.0,
            retry_budget_seconds=0.0,
            call_type="worktree_title",
        )

        metadata = backend._requests_metadata[0]
        assert metadata is not None
        assert metadata["call_type"] == "worktree_title"


def _mistral_provider(api_base: str) -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base=api_base,
        api_key_env_var="MISTRAL_API_KEY",
        backend=Backend.MISTRAL,
    )


def _custom_front_config(**kwargs):
    """A Mistral provider that is not the public API."""
    return build_test_vibe_config(
        providers=[_mistral_provider("https://llm.acme.internal/v1")],
        models=[
            ModelConfig(
                name="mistral-vibe-cli-latest",
                provider="mistral",
                alias="devstral-latest",
            )
        ],
        active_model="devstral-latest",
        **kwargs,
    )


class TestPublicMistralOrigin:
    @pytest.mark.parametrize(
        ("api_base", "expected"),
        [
            ("https://api.mistral.ai/v1", True),
            ("HTTPS://API.MISTRAL.AI:443/v1", True),
            ("https://customer.mistral.ai/v1", False),
            ("https://api.mistral.ai.example.com/v1", False),
            ("http://api.mistral.ai/v1", False),
            ("https://api.mistral.ai:8443/v1", False),
            ("https://api.mistral.ai:not-a-port/v1", False),
        ],
    )
    def test_only_the_public_api_is_assumed_to_serve_the_fast_model(
        self, api_base: str, expected: bool
    ) -> None:
        assert _is_public_mistral_api(api_base) is expected


class TestDiscoveryUsesTheProbe:
    def test_custom_front_keeps_the_active_model_while_unprobed(self) -> None:
        config = _custom_front_config()

        model, provider = select_utility_model(config)

        assert model.alias == "devstral-latest"
        assert provider.name == "mistral"
        assert not is_fast_utility_model(config)

    def test_custom_front_uses_the_fast_model_once_probed(self) -> None:
        config = _custom_front_config()
        MODEL_AVAILABILITY.remember(
            provider=_mistral_provider("https://llm.acme.internal/v1"),
            model=FAST_MODEL_CANDIDATES[0],
            available=True,
        )

        model, _ = select_utility_model(config)

        assert model.name == FAST_MODEL_CANDIDATES[0].name
        assert is_fast_utility_model(config)

    def test_a_negative_probe_overrides_the_public_api_assumption(self) -> None:
        config = build_test_vibe_config()
        for candidate in FAST_MODEL_CANDIDATES:
            MODEL_AVAILABILITY.remember(
                provider=_mistral_provider("https://api.mistral.ai/v1"),
                model=candidate,
                available=False,
            )

        model, _ = select_utility_model(config)

        assert model.alias == config.get_active_model().alias
        assert not is_fast_utility_model(config)

    def test_the_second_candidate_is_used_when_only_it_is_served(self) -> None:
        config = _custom_front_config()
        provider = _mistral_provider("https://llm.acme.internal/v1")
        MODEL_AVAILABILITY.remember(
            provider=provider, model=FAST_MODEL_CANDIDATES[0], available=False
        )
        MODEL_AVAILABILITY.remember(
            provider=provider, model=FAST_MODEL_CANDIDATES[1], available=True
        )

        model, _ = select_utility_model(config)

        assert model.name == FAST_MODEL_CANDIDATES[1].name
        assert is_fast_utility_model(config)


class TestFeatureOverrides:
    def test_a_configured_model_wins_over_discovery(self) -> None:
        config = build_test_vibe_config(
            models=[
                ModelConfig(
                    name="mistral-vibe-cli-latest",
                    provider="mistral",
                    alias="devstral-latest",
                ),
                ModelConfig(
                    name="house-small-1", provider="mistral", alias="house-small"
                ),
            ],
            active_model="devstral-latest",
            utility_models={"smart_approve": "house-small"},
        )

        classifier, _ = select_utility_model(
            config, feature=UtilityFeature.SMART_APPROVE
        )
        title, _ = select_utility_model(config, feature=UtilityFeature.TITLE)

        assert classifier.name == "house-small-1"
        assert title.name == FAST_MODEL_CANDIDATES[0].name

    def test_the_active_sentinel_pins_a_feature_to_the_session_model(self) -> None:
        config = build_test_vibe_config(utility_models={"title": "active"})

        model, _ = select_utility_model(config, feature=UtilityFeature.TITLE)

        assert model.alias == config.get_active_model().alias
        assert not is_fast_utility_model(config, feature=UtilityFeature.TITLE)

    def test_an_unknown_alias_falls_back_to_discovery(self) -> None:
        config = build_test_vibe_config(utility_models={"title": "no-such-model"})

        model, _ = select_utility_model(config, feature=UtilityFeature.TITLE)

        assert model.name == FAST_MODEL_CANDIDATES[0].name

    @pytest.mark.parametrize("name", ["big-1", FAST_MODEL_CANDIDATES[1].name])
    def test_an_override_is_never_reported_as_the_cheap_tier(self, name: str) -> None:
        config = build_test_vibe_config(
            models=[
                ModelConfig(
                    name="mistral-vibe-cli-latest",
                    provider="mistral",
                    alias="devstral-latest",
                ),
                ModelConfig(name=name, provider="mistral", alias="big"),
            ],
            active_model="devstral-latest",
            utility_models={"title": "big"},
        )

        assert not is_fast_utility_model(config, feature=UtilityFeature.TITLE)


class _RecordingSource:
    def __init__(self, verdicts: dict[str, bool] | None = None) -> None:
        self.verdicts = verdicts or {}
        self.asked: list[list[str]] = []

    async def check(self, *, provider, models, timeout_seconds):
        self.asked.append([model.name for model in models])
        return self.verdicts


def _install_source(monkeypatch, source) -> None:
    monkeypatch.setattr(MODEL_AVAILABILITY, "_source", source)


class TestEnsureUtilityModelsProbed:
    @pytest.mark.asyncio
    async def test_asks_about_the_candidates_in_preference_order(
        self, monkeypatch
    ) -> None:
        source = _RecordingSource()
        _install_source(monkeypatch, source)

        await ensure_utility_models_probed(_custom_front_config())

        assert source.asked == [[candidate.name for candidate in FAST_MODEL_CANDIDATES]]

    @pytest.mark.asyncio
    async def test_the_verdict_decides_the_utility_model(self, monkeypatch) -> None:
        _install_source(
            monkeypatch,
            _RecordingSource({
                FAST_MODEL_CANDIDATES[0].name: False,
                FAST_MODEL_CANDIDATES[1].name: True,
            }),
        )
        config = _custom_front_config()

        await ensure_utility_models_probed(config)
        model, _ = select_utility_model(config)

        assert model.name == FAST_MODEL_CANDIDATES[1].name

    @pytest.mark.asyncio
    async def test_candidates_the_allowlist_excludes_are_not_asked_about(
        self, monkeypatch
    ) -> None:
        source = _RecordingSource()
        _install_source(monkeypatch, source)

        await ensure_utility_models_probed(
            _custom_front_config(
                allowed_models=["mistral-vibe-cli-latest", "mistral-small-latest"]
            )
        )

        assert source.asked == [[FAST_MODEL_CANDIDATES[1].name]]

    @pytest.mark.asyncio
    async def test_asks_nothing_when_every_feature_names_a_model(
        self, monkeypatch
    ) -> None:
        source = _RecordingSource()
        _install_source(monkeypatch, source)

        await ensure_utility_models_probed(
            _custom_front_config(
                utility_models={"title": "active", "smart_approve": "active"}
            )
        )

        assert source.asked == []

    @pytest.mark.asyncio
    async def test_the_test_kill_switch_asks_nothing(self, monkeypatch) -> None:
        source = _RecordingSource()
        _install_source(monkeypatch, source)
        monkeypatch.setenv("VIBE_TEST_DISABLE_MODEL_PROBE", "1")

        await ensure_utility_models_probed(_custom_front_config())

        assert source.asked == []

    @pytest.mark.asyncio
    async def test_a_failing_source_does_not_fail_session_open(
        self, monkeypatch, caplog
    ) -> None:
        class FailingSource:
            async def check(self, **_):
                raise RuntimeError("keyring unavailable")

        _install_source(monkeypatch, FailingSource())

        await ensure_utility_models_probed(_custom_front_config())

        assert "availability check failed" in caplog.text

    @pytest.mark.asyncio
    async def test_a_source_ignoring_its_budget_cannot_hold_session_open(
        self, monkeypatch
    ) -> None:
        import time

        class HangingSource:
            async def check(self, **_):
                await asyncio.sleep(10)
                return {}

        _install_source(monkeypatch, HangingSource())
        start = time.perf_counter()

        await ensure_utility_models_probed(_custom_front_config(), timeout_seconds=0.05)

        assert time.perf_counter() - start < 1.0

    @pytest.mark.asyncio
    async def test_asks_nothing_when_no_feature_is_in_use(self, monkeypatch) -> None:
        source = _RecordingSource()
        _install_source(monkeypatch, source)

        await ensure_utility_models_probed(_custom_front_config(), features=())

        assert source.asked == []

    @pytest.mark.asyncio
    async def test_asks_nothing_without_a_mistral_provider(self, monkeypatch) -> None:
        source = _RecordingSource()
        _install_source(monkeypatch, source)

        await ensure_utility_models_probed(_anthropic_only_config())

        assert source.asked == []


class TestFeatureLabelsTheCall:
    @pytest.mark.parametrize(
        ("feature", "call_type"),
        [
            (UtilityFeature.TITLE, "title_generation"),
            (UtilityFeature.SMART_APPROVE, "smart_approve"),
        ],
    )
    @pytest.mark.asyncio
    async def test_the_feature_names_the_request(
        self, monkeypatch, feature: UtilityFeature, call_type: str
    ) -> None:
        backend = FakeBackend([mock_llm_chunk(content="ok")])
        monkeypatch.setattr(utility_completion, "create_backend", lambda **_: backend)

        await run_utility_completion(
            config=build_test_vibe_config(),
            feature=feature,
            system_prompt="system",
            user_content="user",
            max_tokens=24,
            request_timeout_seconds=1.0,
            retry_budget_seconds=0.0,
        )

        metadata = backend._requests_metadata[0]
        assert metadata is not None
        assert metadata["call_type"] == call_type

    @pytest.mark.asyncio
    async def test_a_feature_and_a_call_type_together_is_a_caller_error(
        self, monkeypatch
    ) -> None:
        def fail_create_backend(**_):
            raise AssertionError("a mislabelled call must not be sent")

        monkeypatch.setattr(utility_completion, "create_backend", fail_create_backend)

        with pytest.raises(TypeError):
            await run_utility_completion(
                config=build_test_vibe_config(),
                feature=UtilityFeature.TITLE,
                call_type="smart_approve",
                system_prompt="system",
                user_content="user",
                max_tokens=24,
                request_timeout_seconds=1.0,
                retry_budget_seconds=0.0,
            )

    def test_every_candidate_is_reported_under_its_own_name(self) -> None:
        # Request telemetry reports a utility call's model by alias.
        aliases = [candidate.alias for candidate in FAST_MODEL_CANDIDATES]

        assert len(set(aliases)) == len(aliases)
