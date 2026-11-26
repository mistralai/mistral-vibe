from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from urllib.parse import urlsplit

from vibe.config_values import ThinkingLevel
from vibe.core.config import (
    ModelConfig,
    ProviderConfig,
    UtilityFeature,
    VibeConfigSchema,
)
from vibe.core.llm.completion import CompletionTransport, complete_once
from vibe.core.llm.model_probe import MODEL_AVAILABILITY, PROBE_TIMEOUT_SECONDS
from vibe.core.llm.utility_credentials import UtilityCredentials, holds_credential
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext, TelemetryCallType
from vibe.core.types import LLMMessage, Role
from vibe.core.utils.matching import name_matches
from vibe.observability.logging import logger

# One small model under two names, in preference order: the name the Mistral API
# meters for this CLI, then the public one self-hosted deployments serve. Aliases
# differ so request telemetry shows which one ran.
FAST_MODEL_CANDIDATES: tuple[ModelConfig, ...] = (
    ModelConfig(
        name="mistral-vibe-cli-fast",
        provider="mistral",
        alias="mistral-small",
        input_price=0.1,
        output_price=0.3,
    ),
    ModelConfig(
        name="mistral-small-latest",
        provider="mistral",
        alias="mistral-small-latest",
        input_price=0.1,
        output_price=0.3,
    ),
)

_PUBLIC_MISTRAL_API_ORIGIN = ("https", "api.mistral.ai", 443)

# Test-only kill switch for harnesses that run the real CLI against a mock model.
_DISABLE_MODEL_PROBE_ENV_VAR = "VIBE_TEST_DISABLE_MODEL_PROBE"

__all__ = [
    "FAST_MODEL_CANDIDATES",
    "ensure_utility_models_probed",
    "is_fast_utility_model",
    "run_utility_completion",
    "select_utility_model",
]


def select_utility_model(
    config: VibeConfigSchema,
    *,
    feature: UtilityFeature | None = None,
    credentials: UtilityCredentials | None = None,
) -> tuple[ModelConfig, ProviderConfig]:
    """The model and provider for a utility completion.

    In order: the model ``[utility_models]`` names for ``feature``, a fast Mistral
    model the deployment is known to serve, then the session's active model.
    ``credentials`` is the connection's (see ``UtilityCredentials``); without
    it, a fast model is used only with an API key.
    """
    if feature is not None and (override := config.get_utility_model(feature)):
        return override, config.get_provider_for_model(override)
    if discovered := _discovered_fast_model(config, credentials):
        return discovered
    active = config.get_active_model()
    return active, config.get_provider_for_model(active)


def is_fast_utility_model(
    config: VibeConfigSchema, *, feature: UtilityFeature | None = None
) -> bool:
    """Whether the utility model is a known-cheap fast model; overrides never are.

    False lets callers throttle background use.
    """
    if feature is not None and config.get_utility_model(feature):
        return False
    model, _ = select_utility_model(config, feature=feature)
    return any(model.name == candidate.name for candidate in FAST_MODEL_CANDIDATES)


async def ensure_utility_models_probed(
    config: VibeConfigSchema,
    *,
    features: tuple[UtilityFeature, ...] = tuple(UtilityFeature),
    timeout_seconds: float | None = None,
    credentials: UtilityCredentials | None = None,
) -> None:
    """Learn whether a fast model is served, before routes are derived.

    Runs at session open: never raises, never takes over twice its budget.
    Asks with the connection's ``credentials`` when given.
    """
    if os.environ.get(_DISABLE_MODEL_PROBE_ENV_VAR) == "1":
        return
    budget = PROBE_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    try:
        # Caps a source that overruns its budget.
        async with asyncio.timeout(budget * 2):
            await _ensure_probed(
                config, features=features, budget=budget, credentials=credentials
            )
    except Exception:
        logger.warning(
            "Fast-model availability check failed; "
            "utility features use the active model until it succeeds.",
            exc_info=True,
        )


async def _ensure_probed(
    config: VibeConfigSchema,
    *,
    features: tuple[UtilityFeature, ...],
    budget: float,
    credentials: UtilityCredentials | None,
) -> None:
    if all(config.get_utility_model(feature) for feature in features):
        return
    provider = config.get_mistral_provider()
    if provider is None:
        return
    candidates = [c for c in FAST_MODEL_CANDIDATES if _fast_model_allowed(config, c)]
    if not candidates:
        return
    await MODEL_AVAILABILITY.ensure_first_available(
        provider=provider,
        models=candidates,
        timeout_seconds=budget,
        credentials=credentials,
    )


async def run_utility_completion(  # noqa: PLR0913 - one concern per keyword, all optional past the budgets
    *,
    config: VibeConfigSchema,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    request_timeout_seconds: float,
    retry_budget_seconds: float,
    feature: UtilityFeature | None = None,
    skip_if_no_key: bool = False,
    call_type: TelemetryCallType | None = None,
    launch_context: LaunchContext | None = None,
    session_id: str | None = None,
    telemetry: TelemetryClient | None = None,
    credentials: UtilityCredentials | None = None,
    fixed_model: tuple[ModelConfig, ProviderConfig] | None = None,
    use_model_temperature: bool = False,
    thinking: ThinkingLevel | None = None,
) -> str | None:
    """Run a single completion for a background nicety.

    Owns model selection, budgets and the request metadata so features don't
    re-implement the seam. Returns the raw
    message content; the caller decides how to clean and interpret it.

    ``feature`` picks the model and the telemetry label; calls without one
    (worktree naming) pass ``call_type``. ``launch_context`` and ``session_id``
    give it the same attribution the session's own calls carry. A telemetry
    client, when given, also emits one ``vibe.request_sent`` event for the call.

    ``credentials`` is the connection's (see ``UtilityCredentials``): the
    request then sends what the session's model calls send, the connection's
    sign-in included. Without it, the provider's API key.

    ``skip_if_no_key`` returns None before any network setup when the selected
    provider has no credential to send: no API key and no sign-in. It's for
    callers on a latency-sensitive path with a ready fallback (e.g. worktree
    naming at startup); leave it False so a missing credential surfaces as a
    real backend failure instead of a silent no-op. A keyless local provider
    (empty ``api_key_env_var``, e.g. llama-server) is never skipped, since it
    needs no key to reach.

    The rest keep a caller's own settings, and change nothing when left out:

    - ``fixed_model`` runs this model on this provider, with no fallback to a
      fast model; ``feature`` then only labels the call.
    - ``use_model_temperature`` samples at the model's own temperature and
      ``top_p`` instead of greedily.
    - ``thinking`` replaces the model's configured thinking level for this
      request, sent as if the config had written it; pass a level the model
      offers.
    """
    call_type = _call_type(feature, call_type)
    model, provider = fixed_model or select_utility_model(
        config, feature=feature, credentials=credentials
    )
    if thinking is not None:
        model = model.model_copy(update={"thinking": thinking})
    if skip_if_no_key and not holds_credential(provider, credentials):
        return None

    def report_request(reasoning_effort: str) -> None:
        if telemetry is None:
            return
        telemetry.send_request_sent(
            model=model.alias,
            nb_context_chars=len(system_prompt) + len(user_content),
            nb_context_messages=2,
            nb_prompt_chars=len(user_content),
            call_type=call_type,
            reasoning_effort=reasoning_effort,
        )

    result = await complete_once(
        provider=provider,
        model=model,
        messages=[
            LLMMessage(role=Role.system, content=system_prompt),
            LLMMessage(role=Role.user, content=user_content),
        ],
        temperature=None if use_model_temperature else 0.0,
        max_tokens=max_tokens,
        metadata=build_request_metadata(
            launch_context=launch_context, session_id=session_id, call_type=call_type
        ).model_dump(exclude_none=True),
        transport=replace(
            CompletionTransport.from_config(config),
            timeout_s=request_timeout_seconds,
            retry_budget_s=retry_budget_seconds,
        ),
        on_request=report_request,
        credentials=None
        if credentials is None
        else credentials.for_provider(provider.name),
    )
    return result.text


def _call_type(
    feature: UtilityFeature | None, call_type: TelemetryCallType | None
) -> TelemetryCallType:
    """The telemetry label, derived from the feature so the two cannot disagree."""
    if feature is None:
        return call_type or "secondary_call"
    if call_type is not None:
        raise TypeError("pass feature or call_type, not both: the feature is the label")
    match feature:
        case UtilityFeature.TITLE:
            return "title_generation"
        case UtilityFeature.SMART_APPROVE:
            return "smart_approve"


def _discovered_fast_model(
    config: VibeConfigSchema, credentials: UtilityCredentials | None
) -> tuple[ModelConfig, ProviderConfig] | None:
    """The first fast candidate the deployment serves; keyless providers qualify."""
    provider = config.get_mistral_provider()
    if provider is None:
        return None
    if not holds_credential(provider, credentials):
        return None
    assume_first = _is_public_mistral_api(provider.api_base)
    for index, candidate in enumerate(FAST_MODEL_CANDIDATES):
        if not _fast_model_allowed(config, candidate):
            continue
        known = MODEL_AVAILABILITY.peek(
            provider=provider, model=candidate, credentials=credentials
        )
        if known:
            return candidate, provider
        if known is None and index == 0 and assume_first:
            # Unprobed public API: keep today's behaviour until the probe answers.
            return candidate, provider
    return None


def _fast_model_allowed(config: VibeConfigSchema, model: ModelConfig) -> bool:
    # By name, like ``available_models``: aliases are user-controlled.
    return not config.allowed_models or name_matches(model.name, config.allowed_models)


def _is_public_mistral_api(api_base: str) -> bool:
    parsed = urlsplit(api_base)
    try:
        port = parsed.port
    except ValueError:
        return False
    effective_port = 443 if port is None and parsed.scheme.lower() == "https" else port
    origin = (parsed.scheme.lower(), (parsed.hostname or "").lower(), effective_port)
    return origin == _PUBLIC_MISTRAL_API_ORIGIN
