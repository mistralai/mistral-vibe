"""The model facts of a session's completion routes, compiled from Vibe config.

A model's own settings are the explicit layer of the local Runtime's completion
profile, and the provider's model catalogue is the next one; the provider's
backend picks the adapter that cuts them to what it can send. Each derivation
compiles its routes once: the agent's facts go to Core, and the compaction
route's only steer how images reach it.

The catalogue is asked once per process and endpoint, from the derivation's
worker thread; a derivation on the event loop only reads what is cached, so it
never waits on the network. A session saves the agent model's catalogue
answer, so a reopen reuses it instead of asking again.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import json
from typing import Any

import httpx

from mistralai_vibe_local_harness.vibe import (
    LocalRuntimeAdapterConfig,
    adapter_capabilities,
)
from mistralai_vibe_local_harness.vibe.completion_profile import (
    PROCESS_CATALOGUE_CACHE,
    REASONING_LEVELS,
    AnthropicCatalogueFetcher,
    CatalogueEntry,
    CatalogueFetcher,
    CatalogueLookup,
    CompiledProfile,
    CompletionWarning,
    ExplicitFacts,
    InputModality,
    MistralCatalogueFetcher,
    ModelFacts,
    NoCatalogueFetcher,
    ProfileError,
    ReasoningControl,
    ReasoningLevel,
    ReasoningSupport,
    catalogue_endpoint,
    compile_profile,
    credential_fingerprint,
    lookup_catalogue,
)
from vibe.app_server._agent_socket import OUTCOME_ERROR_FIELD
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.protocol import ProtocolErrorCode
from vibe.app_server.run_export import RunOutcome
from vibe.config_values import THINKING_LEVELS, ThinkingLevel
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.types import Backend
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

type CatalogueFetcherFactory = Callable[
    [ProviderConfig, httpx.AsyncClient], CatalogueFetcher
]


def provider_catalogue_fetcher(
    provider: ProviderConfig, client: httpx.AsyncClient
) -> CatalogueFetcher:
    """The catalogue fetcher for ``provider``, with the key its completions use."""
    key = resolve_api_key(provider.api_key_env_var)
    headers = dict(provider.extra_headers)
    if provider.backend == Backend.MISTRAL:
        return MistralCatalogueFetcher(
            client, provider.api_base, api_key=key, headers=headers
        )
    if provider.api_style == "anthropic":
        return AnthropicCatalogueFetcher(
            client, provider.api_base, api_key=key, headers=headers
        )
    return NoCatalogueFetcher()


# ``None`` asks no catalogue; tests set it so no session reaches the network.
catalogue_fetcher_factory: CatalogueFetcherFactory | None = provider_catalogue_fetcher


class ModelFactsError(SessionBackendError):
    """A model's settings no completion profile can hold on its provider.

    At session open it is the open error naming ``config_error``; a config
    write that would cause it is refused with this reason as ``invalid_params``.
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            ProtocolErrorCode.INTERNAL_ERROR,
            message,
            {OUTCOME_ERROR_FIELD: RunOutcome.CONFIG_ERROR.value},
        )


@dataclass(frozen=True, slots=True)
class CatalogueSnapshot:
    """The catalogue's answer for one model at one endpoint, as a session saves it."""

    endpoint: str
    model: str
    entry: CatalogueEntry

    def to_pin(self) -> str:
        """The snapshot as the JSON the session stores."""
        reasoning = self.entry.reasoning
        return json.dumps(
            {
                "endpoint": self.endpoint,
                "model": self.model,
                "context_window": self.entry.context_window,
                "max_output_tokens": self.entry.max_output_tokens,
                "input_modalities": None
                if self.entry.input_modalities is None
                else list(self.entry.input_modalities),
                "reasoning": None
                if reasoning is None
                else {
                    "levels": list(reasoning.levels),
                    "can_turn_off": reasoning.can_turn_off,
                    "control": reasoning.control.value,
                },
            },
            sort_keys=True,
        )

    @classmethod
    def from_pin(cls, value: str) -> CatalogueSnapshot | None:
        """The snapshot ``to_pin`` wrote, or ``None`` when ``value`` is not one."""
        try:
            data = json.loads(value)
            return cls(
                endpoint=_text(data["endpoint"]),
                model=_text(data["model"]),
                entry=CatalogueEntry(
                    context_window=_count(data.get("context_window")),
                    max_output_tokens=_count(data.get("max_output_tokens")),
                    input_modalities=_modalities(data.get("input_modalities")),
                    reasoning=_reasoning(data.get("reasoning")),
                ),
            )
        except (ValueError, TypeError, KeyError, AttributeError, ProfileError):
            return None


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("not a non-empty string")
    return value


def _count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("not a positive count")
    return value


def _flag(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError("not a boolean")


def _modalities(value: Any) -> tuple[InputModality, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("not a list of input modalities")
    return tuple(InputModality(modality) for modality in value)


def _reasoning(value: Any) -> ReasoningSupport | None:
    if value is None:
        return None
    levels = value["levels"]
    if not isinstance(levels, list) or any(
        level not in REASONING_LEVELS for level in levels
    ):
        raise ValueError("unknown reasoning level")
    return ReasoningSupport(
        tuple(levels),
        can_turn_off=_flag(value["can_turn_off"]) is True,
        control=ReasoningControl(value["control"]),
    )


@dataclass(slots=True)
class SavedCatalogue:
    """The catalogue answer a session saved, set when the session reopens."""

    snapshot: CatalogueSnapshot | None = field(default=None)


@dataclass(frozen=True, slots=True)
class SessionModelFacts:
    """The facts of the agent and compaction routes, and the agent route's warnings.

    The compaction route's warnings are dropped: of its facts only image support,
    max output and reasoning levels are read, so a guessed window or a moved
    threshold there changes nothing.
    """

    agent: ModelFacts
    compaction: ModelFacts
    warnings: tuple[CompletionWarning, ...]
    # Each route's reasoning levels; None when nobody knows them.
    agent_reasoning: ReasoningSupport | None = None
    compaction_reasoning: ReasoningSupport | None = None
    # Each route's thinking level: the config's, else the model's lowest.
    agent_thinking: ThinkingLevel = "off"
    compaction_thinking: ThinkingLevel = "off"
    # The agent model's catalogue answer for the session to save; None when the
    # catalogue gave none.
    catalogue: CatalogueSnapshot | None = None


def compile_session_model_facts(
    provider: ProviderConfig,
    agent: ModelConfig,
    compaction: ModelConfig,
    *,
    saved: CatalogueSnapshot | None = None,
) -> SessionModelFacts:
    """Compile both routes on ``provider``, which serves them both.

    ``saved`` is the answer a reopened session saved: it stands in for the
    catalogue when it is for the agent model at this provider's endpoint, so the
    reopen neither asks again nor warns. Raises ``ModelFactsError`` for settings
    no profile can hold, such as a context window too small to compact in.
    """
    reused = _saved_lookup(provider, agent, saved)
    # Each route's model once, less the agent's when its saved answer stands in.
    models = tuple(
        model
        for model in dict.fromkeys((agent.name, compaction.name))
        if reused is None or model != agent.name
    )
    catalogue = _catalogue_lookups(provider, models, fetch=True)
    if reused is not None:
        catalogue = {**catalogue, agent.name: reused}
    agent_catalogue = catalogue.get(agent.name)
    agent_profile = _compile(
        provider,
        agent,
        threshold=agent.auto_compact_threshold,
        catalogue=agent_catalogue,
    )
    compaction_profile = _compile(
        provider, compaction, threshold=None, catalogue=catalogue.get(compaction.name)
    )
    agent_reasoning = agent_profile.profile.format.reasoning
    compaction_reasoning = compaction_profile.profile.format.reasoning
    return SessionModelFacts(
        agent=agent_profile.profile.facts,
        compaction=compaction_profile.profile.facts,
        warnings=(
            *(agent_catalogue.warnings if agent_catalogue is not None else ()),
            *agent_profile.warnings,
        ),
        agent_reasoning=agent_reasoning,
        compaction_reasoning=compaction_reasoning,
        agent_thinking=_thinking(agent, agent_reasoning),
        compaction_thinking=_thinking(compaction, compaction_reasoning),
        catalogue=None
        if agent_catalogue is None or agent_catalogue.entry is None
        else CatalogueSnapshot(
            agent_catalogue.endpoint, agent_catalogue.model, agent_catalogue.entry
        ),
    )


def _saved_lookup(
    provider: ProviderConfig, agent: ModelConfig, saved: CatalogueSnapshot | None
) -> CatalogueLookup | None:
    """``saved`` as a lookup, when it is for ``agent`` at this provider."""
    if saved is None or saved.model != agent.name:
        return None
    if catalogue_fetcher_factory is None:
        return None
    if saved.endpoint != _catalogue_endpoint(provider):
        return None
    return CatalogueLookup(saved.endpoint, saved.model, saved.entry)


@dataclass(frozen=True, slots=True)
class AgentRouteFacts:
    """What a client shows about a model's agent route: where it compacts, the
    thinking level it runs, and the thinking levels it can be set to.
    """

    compaction_threshold: int
    thinking: ThinkingLevel
    thinking_levels: tuple[ThinkingLevel, ...]


def compile_agent_route_facts(
    provider: ProviderConfig, agent: ModelConfig
) -> AgentRouteFacts:
    """The agent route's facts, as far as the catalogue already answered.

    Reads only what the catalogue already answered: this never waits on it.
    """
    catalogue = _catalogue_lookups(provider, (agent.name,), fetch=False)
    compiled = _compile(
        provider,
        agent,
        threshold=agent.auto_compact_threshold,
        catalogue=catalogue.get(agent.name),
    )
    written = compiled.profile.format.reasoning_setting
    return AgentRouteFacts(
        compiled.profile.facts.compaction_threshold,
        # The level Core runs: the written one as written, else the model's
        # lowest, as the session compiles it.
        written
        if written is not None
        else _thinking(agent, compiled.profile.format.reasoning),
        offered_thinking_levels(agent, compiled.profile.format.reasoning),
    )


def compile_agent_compaction_threshold(
    provider: ProviderConfig, agent: ModelConfig
) -> int:
    """The token count at which the agent route compacts."""
    return compile_agent_route_facts(provider, agent).compaction_threshold


def offered_thinking_levels(
    model: ModelConfig, support: ReasoningSupport | None
) -> tuple[ThinkingLevel, ...]:
    """The levels a picker offers.

    The levels the config lists, else the model's known levels, or the default
    set for a model nobody describes, plus the level the config sets when it is
    not among them: the provider, not Vibe, says whether the model takes it.
    """
    if _lists_thinking_levels(model) or support is None:
        offered: tuple[ThinkingLevel, ...] = tuple(model.thinking_levels)
    elif support.can_turn_off or not support.levels:
        offered = ("off", *support.levels)
    else:
        offered = support.levels
    if model.thinking_is_set and model.thinking not in offered:
        offered = tuple(
            level
            for level in THINKING_LEVELS
            if level in offered or level == model.thinking
        )
    return offered


def _lists_thinking_levels(model: ModelConfig) -> bool:
    # Every config layer fills the field, so only a set other than the default
    # five says which levels the model takes.
    return model.thinking_levels_curated


def _catalogue_lookups(
    provider: ProviderConfig, models: tuple[str, ...], *, fetch: bool
) -> dict[str, CatalogueLookup]:
    """The catalogue's answer for each model, fetching what is missing if allowed.

    A fetch runs only off the event loop (derivations run in a worker thread), so
    a derivation on the loop reads the cache and never blocks it.
    """
    factory = catalogue_fetcher_factory
    endpoint = _catalogue_endpoint(provider)
    if factory is None or endpoint is None:
        return {}
    credential = _catalogue_credential(provider)
    found = {
        model: cached
        for model in models
        if (cached := PROCESS_CATALOGUE_CACHE.peek(endpoint, model, credential))
        is not None
    }
    missing = tuple(dict.fromkeys(model for model in models if model not in found))
    if not missing or not fetch or _on_event_loop():
        return found
    return found | asyncio.run(_fetch(provider, factory, missing))


async def _fetch(
    provider: ProviderConfig, factory: CatalogueFetcherFactory, models: tuple[str, ...]
) -> dict[str, CatalogueLookup]:
    async with VibeAsyncHTTPClient(verify=build_ssl_context()) as client:
        fetcher = factory(provider, client)
        # One bounded lookup per model, all at once: a slow catalogue costs the
        # session one timeout, not one per model.
        lookups = await asyncio.gather(
            *(lookup_catalogue(fetcher, model) for model in models)
        )
        return dict(zip(models, lookups, strict=True))


def _catalogue_endpoint(provider: ProviderConfig) -> str | None:
    """The provider's catalogue URL, or ``None`` when it has none or no key."""
    has_catalogue = (
        provider.backend == Backend.MISTRAL or provider.api_style == "anthropic"
    )
    if not has_catalogue or not resolve_api_key(provider.api_key_env_var):
        return None
    return catalogue_endpoint(provider.api_base)


def _catalogue_credential(provider: ProviderConfig) -> str:
    """The fingerprint of what ``provider_catalogue_fetcher`` sends: its cache key."""
    return credential_fingerprint(
        resolve_api_key(provider.api_key_env_var), provider.extra_headers
    )


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _compile(
    provider: ProviderConfig,
    model: ModelConfig,
    *,
    threshold: int | None,
    catalogue: CatalogueLookup | None,
) -> CompiledProfile:
    adapter = LocalRuntimeAdapterConfig(
        backend=str(provider.backend),
        api_style=provider.api_style,
        emits_finish_reason=provider.emits_finish_reason,
    )
    try:
        return compile_profile(
            model.name,
            adapter_capabilities(adapter),
            ExplicitFacts(
                context_window=model.max_context_length,
                max_output_tokens=model.max_output_tokens,
                # Unset (None) leaves the input modalities to the catalogue and the
                # known-model table. Vibe still only offers images (attachments,
                # the describer) to a model whose config says so.
                input_modalities=_supports_images_modalities(model.supports_images),
                compaction_threshold=threshold,
                # Unset fits nothing: the route then takes the model's lowest.
                reasoning=model.thinking if model.thinking_is_set else None,
                reasoning_levels=_written_reasoning_levels(model),
            ),
            catalogue.entry if catalogue is not None else None,
        )
    except ProfileError as error:
        raise ModelFactsError(
            f"Model '{model.alias}' cannot be used with provider "
            f"'{provider.name}': {error}."
        ) from error


def _written_reasoning_levels(model: ModelConfig) -> ReasoningSupport | None:
    """The levels the config lists, as `ExplicitFacts.reasoning_levels` takes them.

    A set with no level, only off, says the user wants no reasoning, not that the
    model has none: the catalogue or the known row still says whether off has to
    be sent for the model to stop.
    """
    if not _lists_thinking_levels(model):
        return None
    written = set(model.thinking_levels)
    levels: tuple[ReasoningLevel, ...] = tuple(
        level for level in REASONING_LEVELS if level in written
    )
    if not levels:
        return None
    return ReasoningSupport(levels, can_turn_off="off" in written)


def _thinking(model: ModelConfig, support: ReasoningSupport | None) -> ThinkingLevel:
    """The config's level, else the model's lowest setting.

    The lowest is off where the model can stop or its levels are unknown, else its
    lowest level: an unset level must not ask a model that always reasons for off.
    """
    if model.thinking_is_set:
        return model.thinking
    if support is None or support.can_turn_off:
        return "off"
    return support.levels[0]


def _supports_images_modalities(
    supports_images: bool | None,
) -> tuple[InputModality, ...] | None:
    """The input modalities a model's ``supports_images`` states; unset states none."""
    if supports_images is None:
        return None
    if supports_images:
        return (InputModality.TEXT, InputModality.IMAGE)
    return (InputModality.TEXT,)
