"""The model facts a session compiles from Vibe config, per provider adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx2
import pytest

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from mistralai_vibe_local_harness.vibe.completion_profile import (
    CatalogueEntry,
    InputModality,
    ReasoningControl,
    ReasoningSupport,
    WarningCode,
    catalogue_endpoint,
    credential_fingerprint,
)
from vibe import __version__
from vibe.app_server import _completion_facts
from vibe.app_server._agent_socket import OUTCOME_ERROR_FIELD
from vibe.app_server._completion_facts import (
    ModelFactsError,
    compile_agent_compaction_threshold,
    compile_agent_route_facts,
    compile_session_model_facts,
    provider_catalogue_fetcher,
)
from vibe.app_server.run_export import RunOutcome
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.types import Backend

_TEXT_AND_IMAGE = (InputModality.TEXT, InputModality.IMAGE)
_TEXT_ONLY = (InputModality.TEXT,)


def _provider(
    *,
    backend: Backend = Backend.MISTRAL,
    api_style: str = "openai",
    emits_finish_reason: bool = True,
) -> ProviderConfig:
    return ProviderConfig(
        name="p",
        api_base="https://provider.example/v1",
        backend=backend,
        api_style=api_style,
        emits_finish_reason=emits_finish_reason,
    )


def _model(name: str = "custom-model", **fields: object) -> ModelConfig:
    return ModelConfig.model_validate({
        "name": name,
        "provider": "p",
        "alias": name,
        **fields,
    })


def test_explicit_model_settings_are_the_facts() -> None:
    """*Prepare*: A model declaring its window, output cap, images and threshold.
    *Do*: Compile the session's facts.
    *Assert*: Each declared value is a fact, and nothing warns.
    """
    # Prepare
    model = _model(
        max_context_length=128_000,
        max_output_tokens=16_000,
        supports_images=True,
        auto_compact_threshold=90_000,
    )

    # Do
    facts = compile_session_model_facts(_provider(), model, model)

    # Assert
    assert facts.agent.context_window == 128_000
    assert facts.agent.max_output_tokens == 16_000
    assert facts.agent.input_modalities == _TEXT_AND_IMAGE
    assert facts.agent.compaction_threshold == 90_000
    assert facts.warnings == ()


def test_an_unset_window_is_guessed_with_a_warning() -> None:
    """*Prepare*: A model no table knows, declaring nothing.
    *Do*: Compile the session's facts.
    *Assert*: The default window and its default threshold apply, images stay
    off as the config says, and the guess is a warning.
    """
    # Do
    facts = compile_session_model_facts(_provider(), _model(), _model())

    # Assert
    assert facts.agent.context_window == 262_144
    assert facts.agent.compaction_threshold == 202_144
    assert facts.agent.input_modalities == _TEXT_ONLY
    assert [warning.code for warning in facts.warnings] == [WarningCode.WINDOW_GUESSED]


def test_a_known_model_takes_its_window_from_the_table() -> None:
    """*Prepare*: A known Mistral model with no declared window.
    *Do*: Compile its threshold.
    *Assert*: It is the default for the known 262,144-token window, unwarned.
    """
    # Do
    facts = compile_session_model_facts(
        _provider(), _model("mistral-medium-3.5"), _model("mistral-medium-3.5")
    )

    # Assert
    assert facts.agent.context_window == 262_144
    assert facts.agent.compaction_threshold == 202_144
    assert facts.warnings == ()


def test_a_known_model_takes_its_input_modalities_from_the_table_when_unset() -> None:
    """*Prepare*: A known image model, with images unset and set off.
    *Do*: Compile the session's facts for each.
    *Assert*: Unset takes the table's input modalities; an explicit false wins.
    """
    # Do
    unset = compile_session_model_facts(
        _provider(), _model("mistral-medium-3.5"), _model("mistral-medium-3.5")
    )
    off = _model("mistral-medium-3.5", supports_images=False)
    declared_off = compile_session_model_facts(_provider(), off, off)

    # Assert
    assert unset.agent.input_modalities == _TEXT_AND_IMAGE
    assert declared_off.agent.input_modalities == _TEXT_ONLY


def test_only_the_agent_route_warns() -> None:
    """*Prepare*: A known agent model and an unknown compaction model.
    *Do*: Compile the session's facts.
    *Assert*: The compaction route's guessed window does not warn: of its
    facts only image support is read.
    """
    # Do
    facts = compile_session_model_facts(
        _provider(), _model("mistral-medium-3.5"), _model("unknown-compactor")
    )

    # Assert
    assert facts.compaction.context_window == 262_144
    assert facts.warnings == ()


def test_a_threshold_past_the_window_is_a_config_error() -> None:
    """*Prepare*: A threshold equal to the model's window.
    *Do*: Compile the session's facts and the agent route's threshold.
    *Assert*: Both are refused with the window's range; neither lowers the threshold.
    """
    # Prepare
    model = _model(max_context_length=100_000, auto_compact_threshold=100_000)
    message = (
        r"compaction threshold 100000 is outside 50000\.\.76000 for a "
        r"100000-token context window"
    )

    # Do / Assert
    with pytest.raises(ModelFactsError, match=message) as raised:
        compile_session_model_facts(_provider(), model, model)
    with pytest.raises(ModelFactsError, match=message):
        compile_agent_compaction_threshold(_provider(), model)
    assert raised.value.data == {OUTCOME_ERROR_FIELD: RunOutcome.CONFIG_ERROR.value}


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        (_provider(), "context window of 10000 tokens is too small"),
        (_provider(backend=Backend.GENERIC, api_style="soap"), "no completion adapter"),
    ],
    ids=["window-too-small", "unknown-api-style"],
)
def test_settings_no_profile_can_hold_are_a_config_error(
    provider: ProviderConfig, message: str
) -> None:
    """*Prepare*: A window too small to compact in, or an API style no adapter
    speaks.
    *Do*: Compile the session's facts.
    *Assert*: The error names the model, the provider and the cause, and ends a
    run as ``config_error``.
    """
    # Prepare
    model = _model(max_context_length=10_000)

    # Do
    with pytest.raises(ModelFactsError) as raised:
        compile_session_model_facts(provider, model, model)

    # Assert
    assert "Model 'custom-model' cannot be used with provider 'p'" in str(raised.value)
    assert message in str(raised.value)
    assert raised.value.data == {OUTCOME_ERROR_FIELD: RunOutcome.CONFIG_ERROR.value}


@pytest.mark.parametrize(
    "api_style",
    ["openai", "reasoning", "openai-responses", "anthropic", "vertex-anthropic"],
)
def test_every_generic_api_style_compiles(api_style: str) -> None:
    """*Prepare*: A generic provider of each API style the Runtime speaks.
    *Do*: Compile a model declaring images on it.
    *Assert*: Images reach the facts.
    """
    # Prepare
    model = _model(supports_images=True, max_context_length=128_000)

    # Do
    facts = compile_session_model_facts(
        _provider(backend=Backend.GENERIC, api_style=api_style), model, model
    )

    # Assert
    assert facts.agent.input_modalities == _TEXT_AND_IMAGE


def test_a_narrowed_level_set_is_the_reasoning_support() -> None:
    """*Prepare*: An unknown model whose config narrows thinking to off and high.
    *Do*: Compile the session's facts.
    *Assert*: The route's reasoning levels are high, and off is allowed.
    """
    # Prepare
    model = _model(thinking_levels=["off", "high"], thinking="high")

    # Do
    facts = compile_session_model_facts(_provider(), model, model)

    # Assert
    assert facts.agent_reasoning is not None
    assert facts.agent_reasoning.levels == ("high",)
    assert facts.agent_reasoning.can_turn_off is True


def test_the_default_level_set_leaves_reasoning_unknown() -> None:
    """*Prepare*: An unknown model that keeps the default thinking levels.
    *Do*: Compile the session's facts.
    *Assert*: Its reasoning levels stay unknown.
    """
    # Do
    facts = compile_session_model_facts(_provider(), _model(), _model())

    # Assert
    assert facts.agent_reasoning is None
    assert facts.compaction_reasoning is None


def test_the_configs_level_set_wins_over_a_known_models() -> None:
    """*Prepare*: Mistral Large 4, whose table row lists low, high and max, while
    its config lists low, medium and high.
    *Do*: Compile the session's facts at each listed level.
    *Assert*: The config's levels apply, and each level runs as written.
    """
    # Prepare
    provider = _provider()

    for level in ("low", "medium", "high"):
        model = _model(
            "mistral-large-4", thinking=level, thinking_levels=["low", "medium", "high"]
        )

        # Do
        facts = compile_session_model_facts(provider, model, model)

        # Assert
        assert facts.agent_reasoning == ReasoningSupport(
            ("low", "medium", "high"), can_turn_off=False
        )
        assert facts.agent_thinking == level
        assert facts.warnings == ()


def test_a_level_outside_the_configs_level_set_runs_and_is_offered() -> None:
    """*Prepare*: A model whose config lists low, high and max, set to medium.
    *Do*: Compile the session's facts and the picker's levels.
    *Assert*: The session runs at medium, and the picker offers it beside the
    listed levels so the user can switch away from it: the provider, not Vibe,
    says whether the model takes it.
    """
    # Prepare
    provider = _provider()
    model = _model(
        "mistral-large-4", thinking="medium", thinking_levels=["low", "high", "max"]
    )

    # Do
    facts = compile_session_model_facts(provider, model, model)
    route = compile_agent_route_facts(provider, model)

    # Assert
    assert facts.agent_thinking == "medium"
    assert route.thinking_levels == ("low", "medium", "high", "max")


@pytest.mark.parametrize(
    ("levels", "thinking", "expected_levels", "expected_offered"),
    [
        (["low", "turbo"], "turbo", ("low", "turbo"), ("low", "turbo")),
        (
            ["off", "high", "turbo", "low"],
            "turbo",
            ("low", "turbo", "high"),
            ("off", "high", "turbo", "low"),
        ),
        (["low", "high"], "ultra", ("low", "high"), ("low", "high", "ultra")),
    ],
    ids=["listed", "ranked-around-own-level", "unlisted-goes-last"],
)
def test_a_level_of_the_models_own_runs_as_written_over_a_known_model(
    levels: list[str],
    thinking: str,
    expected_levels: tuple[str, ...],
    expected_offered: tuple[str, ...],
) -> None:
    """*Prepare*: A known model, served on an OpenAI-compatible route, whose table
    row stops at high, while its config lists a level of its own, and sets it or a
    word it does not list.
    *Do*: Compile the session's facts and the picker's levels.
    *Assert*: The config's levels are the model's, lowest first with the own level
    kept in its place; the session runs the level as written, and the picker
    offers the list in the config's order, an unlisted level last.
    """
    # Prepare
    provider = _provider(backend=Backend.GENERIC)
    model = _model("mistral-large-4", thinking=thinking, thinking_levels=levels)

    # Do
    facts = compile_session_model_facts(provider, model, model)
    route = compile_agent_route_facts(provider, model)

    # Assert
    assert facts.agent_reasoning is not None
    assert facts.agent_reasoning.levels == expected_levels
    assert facts.agent_thinking == thinking
    assert route.thinking == thinking
    assert route.thinking_levels == expected_offered


def test_a_mistral_route_runs_any_level_as_written() -> None:
    """*Prepare*: A Mistral model whose config lists a level of its own first and
    is set to it.
    *Do*: Compile the session's facts.
    *Assert*: The level runs as written with every listed level kept: the harness
    does not judge it. The Mistral SDK leaves it out of the request, which the
    config warns about.
    """
    # Prepare
    model = _model(thinking="turbo", thinking_levels=["turbo", "high"])

    # Do
    facts = compile_session_model_facts(_provider(), model, model)

    # Assert
    assert facts.agent_thinking == "turbo"
    assert facts.agent_reasoning is not None
    assert facts.agent_reasoning.levels == ("turbo", "high")


def test_the_configs_own_levels_win_over_the_catalogues(
    catalogue: _CountingFetcher,
) -> None:
    """*Prepare*: An Anthropic catalogue that lists low and high for the model, and
    a config that lists low and a level of the model's own, set to it.
    *Do*: Compile the session's facts.
    *Assert*: The config's levels apply and its level runs as written; the
    catalogue still says how the model takes reasoning.
    """
    # Prepare
    catalogue.entry = CatalogueEntry(
        reasoning=ReasoningSupport(("low", "high"), can_turn_off=True)
    )
    provider = _provider(backend=Backend.GENERIC, api_style="anthropic").model_copy(
        update={"api_key_env_var": _KEY_VAR}
    )
    model = _model(thinking="turbo", thinking_levels=["low", "turbo"])

    # Do
    facts = compile_session_model_facts(provider, model, model)

    # Assert
    assert facts.agent_reasoning == ReasoningSupport(
        ("low", "turbo"), can_turn_off=False
    )
    assert facts.agent_thinking == "turbo"
    assert catalogue.fetched == ["custom-model"]


def test_a_level_outside_the_known_levels_runs_when_the_config_lists_none() -> None:
    """*Prepare*: Mistral Large 4, whose known levels are low, high and max, set to xhigh
    with no level set in its config.
    *Do*: Compile the session's facts.
    *Assert*: The session runs at xhigh: with no listed levels, Vibe sends the level
    and the provider says whether the model takes it.
    """
    # Prepare
    model = _model("mistral-large-4", thinking="xhigh")

    # Do
    facts = compile_session_model_facts(_provider(), model, model)

    # Assert
    assert facts.agent_thinking == "xhigh"


@pytest.mark.parametrize("levels", [[], ["off"]])
def test_an_off_only_level_set_keeps_the_models_known_reasoning(
    levels: list[str],
) -> None:
    """*Prepare*: Claude Opus 4.8, which reasons and can stop, with a config that
    lists no level but off.
    *Do*: Compile the session's facts.
    *Assert*: The model keeps its known reasoning, so off goes out as a disabled
    thinking block: read as a model without reasoning, no field would be sent and
    the model would keep thinking.
    """
    # Prepare
    model = _model("claude-opus-4-8", thinking="off", thinking_levels=levels)
    provider = _provider(backend=Backend.GENERIC, api_style="anthropic")

    # Do
    facts = compile_session_model_facts(provider, model, model)

    # Assert
    assert facts.agent_reasoning is not None
    assert facts.agent_reasoning.levels
    assert facts.agent_reasoning.can_turn_off
    assert facts.agent_thinking == "off"


@pytest.mark.parametrize(("agent_cap", "warns"), [(None, False), (1_000, True)])
def test_an_agent_cap_too_small_to_reason_warns(
    agent_cap: int | None, warns: bool
) -> None:
    model = _model("claude-haiku-4-5", thinking="high")
    provider = _provider(backend=Backend.GENERIC, api_style="anthropic")

    facts = compile_session_model_facts(
        provider, model, model, agent_max_tokens=agent_cap
    )

    codes = [warning.code for warning in facts.warnings]
    assert (WarningCode.REASONING_BUDGET_UNAVAILABLE in codes) is warns


def test_reasoning_off_on_a_model_that_always_reasons_runs_as_written() -> None:
    """*Prepare*: A known model that cannot turn reasoning off, configured off.
    *Do*: Compile the session's facts.
    *Assert*: The session runs at off: the provider, not Vibe, refuses it.
    """
    # Prepare
    model = _model("claude-opus-5-5", thinking="off")
    provider = _provider(backend=Backend.GENERIC, api_style="anthropic")

    # Do
    facts = compile_session_model_facts(provider, model, model)

    # Assert
    assert facts.agent_thinking == "off"


_KEY_VAR = "CATALOGUE_TEST_API_KEY"


@dataclass
class _CountingFetcher:
    entry: CatalogueEntry | None
    endpoint: str = catalogue_endpoint("https://provider.example/v1")
    # What the real fetcher sends for the keyed provider below.
    credential: str = credential_fingerprint("test-key", {})
    fail: bool = False
    fetched: list[str] = field(default_factory=list)
    in_flight: int = 0
    most_in_flight: int = 0

    async def fetch(self, model: str) -> CatalogueEntry | None:
        self.fetched.append(model)
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        # Yield so another lookup started alongside this one runs meanwhile.
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        if self.fail:
            raise httpx2.ConnectError("refused")
        return self.entry


def _keyed_provider() -> ProviderConfig:
    return _provider().model_copy(update={"api_key_env_var": _KEY_VAR})


@pytest.fixture
def catalogue(monkeypatch: pytest.MonkeyPatch) -> _CountingFetcher:
    fetcher = _CountingFetcher(
        CatalogueEntry(context_window=500_000, input_modalities=_TEXT_AND_IMAGE)
    )
    monkeypatch.setenv(_KEY_VAR, "test-key")
    monkeypatch.setattr(
        _completion_facts,
        "catalogue_fetcher_factory",
        lambda _provider, _client: fetcher,
    )
    return fetcher


def test_the_catalogue_fills_what_the_model_leaves_unset(
    catalogue: _CountingFetcher,
) -> None:
    """*Prepare*: A Mistral provider whose catalogue knows the model's window.
    *Do*: Compile the session's facts twice.
    *Assert*: The window comes from the catalogue, asked once; the facts match a
    model declaring that window, and its own image setting still wins.
    """
    # Prepare
    model = _model(supports_images=False)
    declared = _model(supports_images=False, max_context_length=500_000)

    # Do
    first = compile_session_model_facts(_keyed_provider(), model, model)
    second = compile_session_model_facts(_keyed_provider(), model, model)
    without_catalogue = compile_session_model_facts(_provider(), declared, declared)

    # Assert
    assert first.agent.context_window == second.agent.context_window == 500_000
    assert first.agent == without_catalogue.agent
    assert first.agent.input_modalities == _TEXT_ONLY
    assert first.warnings == ()
    assert catalogue.fetched == ["custom-model"]


def test_the_catalogue_fills_the_input_modalities_the_model_leaves_unset(
    catalogue: _CountingFetcher,
) -> None:
    """*Prepare*: A catalogue reporting image input for an unknown model.
    *Do*: Compile the facts of that model with images unset.
    *Assert*: The input modalities come from the catalogue.
    """
    # Do
    facts = compile_session_model_facts(_keyed_provider(), _model(), _model())

    # Assert
    assert facts.agent.input_modalities == _TEXT_AND_IMAGE
    assert catalogue.fetched == ["custom-model"]


def test_the_catalogue_never_sets_the_threshold(catalogue: _CountingFetcher) -> None:
    """*Prepare*: A model declaring its threshold, with a catalogue window.
    *Do*: Compile the session's facts.
    *Assert*: The declared threshold stands.
    """
    # Prepare
    model = _model(auto_compact_threshold=90_000)

    # Do
    facts = compile_session_model_facts(_keyed_provider(), model, model)

    # Assert
    assert facts.agent.context_window == 500_000
    assert facts.agent.compaction_threshold == 90_000


def test_a_failed_catalogue_warns_and_falls_to_the_next_layer(
    catalogue: _CountingFetcher,
) -> None:
    """*Prepare*: A catalogue that refuses the connection.
    *Do*: Compile the session's facts twice.
    *Assert*: The window is guessed, the failure warns each time for the session
    sink to show once, and the catalogue is not asked again.
    """
    # Prepare
    catalogue.fail = True

    # Do
    first = compile_session_model_facts(_keyed_provider(), _model(), _model())
    second = compile_session_model_facts(_keyed_provider(), _model(), _model())

    # Assert
    assert first.agent.context_window == 262_144
    codes = [warning.code for warning in first.warnings]
    assert codes == [WarningCode.CATALOGUE_UNAVAILABLE, WarningCode.WINDOW_GUESSED]
    assert second.warnings == first.warnings
    assert catalogue.fetched == ["custom-model"]


def test_a_client_that_cannot_be_built_costs_the_catalogue_not_the_session(
    catalogue: _CountingFetcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A proxy in the environment the client cannot transport, such
    as SOCKS without `socksio`, so building the catalogue client fails before
    any request.
    *Do*: Compile the session's facts.
    *Assert*: The compile succeeds with a guessed window, the fetcher is never
    asked, and the session start does not fail.
    """

    # Prepare
    def _unbuildable_client(**_kwargs: object) -> httpx2.AsyncClient:
        raise ImportError(
            "Using SOCKS proxy, but the 'socksio' package is not installed."
        )

    monkeypatch.setattr(_completion_facts, "VibeAsyncHTTPClient", _unbuildable_client)

    # Do
    facts = compile_session_model_facts(_keyed_provider(), _model(), _model())

    # Assert
    assert facts.agent.context_window == 262_144
    assert [warning.code for warning in facts.warnings] == [WarningCode.WINDOW_GUESSED]
    assert catalogue.fetched == []


def test_a_compile_on_the_event_loop_reads_only_the_cache(
    catalogue: _CountingFetcher,
) -> None:
    """*Prepare*: A catalogue nothing has asked yet.
    *Do*: Compile on a running event loop, then off it.
    *Assert*: The loop compile neither fetches nor waits; the next one fetches.
    """

    # Prepare
    async def on_loop() -> int:
        facts = compile_session_model_facts(_keyed_provider(), _model(), _model())
        return facts.agent.context_window

    # Do
    loop_window = asyncio.run(on_loop())
    fetched_on_loop = list(catalogue.fetched)
    thread_facts = compile_session_model_facts(_keyed_provider(), _model(), _model())

    # Assert
    assert (loop_window, fetched_on_loop) == (262_144, [])
    assert thread_facts.agent.context_window == 500_000


@pytest.mark.parametrize(
    "provider",
    [
        _provider(),
        _provider(backend=Backend.GENERIC, api_style="openai").model_copy(
            update={"api_key_env_var": _KEY_VAR}
        ),
    ],
    ids=["no-key", "no-catalogue"],
)
def test_a_route_without_a_key_or_catalogue_asks_nothing(
    catalogue: _CountingFetcher, provider: ProviderConfig
) -> None:
    """*Prepare*: A Mistral provider with no key, and an OpenAI-style one.
    *Do*: Compile the session's facts.
    *Assert*: No catalogue is asked.
    """
    # Do
    compile_session_model_facts(provider, _model(), _model())

    # Assert
    assert catalogue.fetched == []


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        '{"endpoint": "e"}',
        '{"endpoint": "e", "model": "m", "context_window": -1}',
        '{"endpoint": "e", "model": "m", '
        '"reasoning": {"levels": [7], "can_turn_off": false, "control": "effort"}}',
        '{"endpoint": "e", "model": "m", '
        '"reasoning": {"levels": ["off"], "can_turn_off": false, "control": "effort"}}',
    ],
    ids=["not-json", "not-object", "no-model", "bad-window", "bad-level", "off-level"],
)
def test_a_saved_catalogue_answer_round_trips_and_a_bad_one_is_ignored(
    stored: str,
) -> None:
    """*Prepare*: A catalogue answer with every fact, a level of the model's own
    among its levels, and a stored value that is not one.
    *Do*: Store the answer and read both back.
    *Assert*: The answer comes back whole; the bad value reads as nothing, so a
    reopen asks the catalogue instead of failing.
    """
    from mistralai_vibe_local_harness.vibe.completion_profile import ReasoningSupport

    # Prepare
    snapshot = _completion_facts.CatalogueSnapshot(
        endpoint="https://provider.example/v1/models",
        model="m",
        entry=CatalogueEntry(
            context_window=131_072,
            max_output_tokens=8_192,
            input_modalities=_TEXT_ONLY,
            reasoning=ReasoningSupport(
                ("low", "turbo", "high"),
                can_turn_off=True,
                control=ReasoningControl.BUDGET_TOKENS,
            ),
        ),
    )

    # Do
    restored = _completion_facts.CatalogueSnapshot.from_pin(snapshot.to_pin())
    ignored = _completion_facts.CatalogueSnapshot.from_pin(stored)

    # Assert
    assert restored == snapshot
    assert ignored is None


def test_a_saved_catalogue_answer_reads_its_standard_levels_lowest_first() -> None:
    """*Prepare*: A saved catalogue answer listing high before low, with a level
    of the model's own between them.
    *Do*: Read it back.
    *Assert*: The standard levels come lowest first and the own level keeps its
    place, so the lowest a compaction asks for is low.
    """
    # Prepare
    stored = (
        '{"endpoint": "e", "model": "m", "reasoning": {"levels": '
        '["high", "turbo", "low"], "can_turn_off": false, "control": "effort"}}'
    )

    # Do
    restored = _completion_facts.CatalogueSnapshot.from_pin(stored)

    # Assert
    assert restored is not None
    assert restored.entry.reasoning is not None
    assert restored.entry.reasoning.levels == ("low", "turbo", "high")


def test_a_reopen_with_a_saved_answer_asks_the_catalogue_nothing(
    catalogue: _CountingFetcher,
) -> None:
    """*Prepare*: A session saved with the catalogue's answer for its model, which
    also compacts.
    *Do*: Compile its facts again with that saved answer, as a reopen does.
    *Assert*: The catalogue is not asked, and the facts match the first compile.
    """
    # Prepare
    model = _model()
    first = compile_session_model_facts(_keyed_provider(), model, model)
    catalogue.fetched.clear()
    _completion_facts.PROCESS_CATALOGUE_CACHE.clear()

    # Do
    reopened = compile_session_model_facts(
        _keyed_provider(), model, model, saved=first.catalogue
    )

    # Assert
    assert first.catalogue is not None
    assert catalogue.fetched == []
    assert reopened.agent == first.agent
    assert reopened.compaction == first.compaction


def test_a_reopen_with_a_saved_answer_asks_only_for_another_compaction_model(
    catalogue: _CountingFetcher,
) -> None:
    """*Prepare*: A session saved with the catalogue's answer for its agent model,
    which compacts with another model.
    *Do*: Compile its facts fresh, then again with that saved answer.
    *Assert*: The fresh compile asks for both models at once; the reopen asks only
    for the compaction model.
    """
    # Prepare
    agent = _model("agent-model")
    compaction = _model("compaction-model")

    # Do
    first = compile_session_model_facts(_keyed_provider(), agent, compaction)
    fresh = list(catalogue.fetched)
    catalogue.fetched.clear()
    _completion_facts.PROCESS_CATALOGUE_CACHE.clear()
    reopened = compile_session_model_facts(
        _keyed_provider(), agent, compaction, saved=first.catalogue
    )

    # Assert
    assert fresh == ["agent-model", "compaction-model"]
    assert catalogue.most_in_flight == 2
    assert catalogue.fetched == ["compaction-model"]
    assert reopened.agent == first.agent
    assert reopened.compaction == first.compaction


@pytest.mark.parametrize(
    ("provider", "name", "lowest"),
    [
        (_provider(), "zai-glm-5-3", "low"),
        (_provider(backend=Backend.GENERIC), "gpt-6-astra", "low"),
        (_provider(), "mistral-medium-3.5", "off"),
        (_provider(), "custom-model", "off"),
    ],
    ids=["glm-5.3", "gpt-6-astra", "can-turn-off", "unknown-model"],
)
def test_an_unset_thinking_takes_the_models_lowest_setting(
    provider: ProviderConfig, name: str, lowest: str
) -> None:
    """*Prepare*: A model entry without `thinking`, on models that always reason,
    one that can stop and one nobody knows.
    *Do*: Compile the session's facts.
    *Assert*: Both routes think at the model's lowest setting: off where it can
    stop, else its lowest level. Nothing is refused.
    """
    # Do
    facts = compile_session_model_facts(provider, _model(name), _model(name))

    # Assert
    assert facts.agent_thinking == lowest
    assert facts.compaction_thinking == lowest


def test_off_on_a_model_that_always_reasons_is_left_to_the_provider() -> None:
    """*Prepare*: GLM 5.3 with thinking set to off.
    *Do*: Compile the session's facts.
    *Assert*: The session runs at off, which the provider refuses with its own
    message naming the levels it takes.
    """
    # Prepare
    model = _model("zai-glm-5-3", thinking="off")

    # Do
    facts = compile_session_model_facts(_provider(), model, model)

    # Assert
    assert facts.agent_thinking == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "user_agent"),
    [
        (_provider(), f"mistral-client-python/Mistral-Vibe/{__version__}"),
        (
            _provider(backend=Backend.GENERIC, api_style="anthropic"),
            f"Mistral-Vibe/{__version__}",
        ),
    ],
)
async def test_the_catalogue_lookup_identifies_vibe(
    provider: ProviderConfig, user_agent: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_VAR, "test-key")
    sent: list[str | None] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.headers.get("user-agent"))
        return httpx2.Response(200, json={"id": "m"})

    keyed = provider.model_copy(update={"api_key_env_var": _KEY_VAR})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        await provider_catalogue_fetcher(keyed, client).fetch("m")

    assert sent == [user_agent]
