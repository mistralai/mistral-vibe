from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

from vibe.core.config import (
    ModelConfig,
    ProviderConfig,
    UtilityFeature,
    VibeConfigSchema,
)
from vibe.core.llm.backend.factory import create_backend
from vibe.core.llm.model_probe import MODEL_AVAILABILITY, PROBE_TIMEOUT_SECONDS
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext, TelemetryCallType
from vibe.core.types import LLMMessage, Role
from vibe.core.utils.matching import name_matches
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import get_user_agent

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
    config: VibeConfigSchema, *, feature: UtilityFeature | None = None
) -> tuple[ModelConfig, ProviderConfig]:
    """The model and provider for a utility completion.

    In order: the model ``[utility_models]`` names for ``feature``, a fast Mistral
    model the deployment is known to serve, then the session's active model.
    """
    if feature is not None and (override := config.get_utility_model(feature)):
        return override, config.get_provider_for_model(override)
    if discovered := _discovered_fast_model(config):
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
) -> None:
    """Learn whether a fast model is served, before routes are derived.

    Runs at session open: never raises, never takes over twice its budget.
    """
    if os.environ.get(_DISABLE_MODEL_PROBE_ENV_VAR) == "1":
        return
    budget = PROBE_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    try:
        # Caps a source that overruns its budget.
        async with asyncio.timeout(budget * 2):
            await _ensure_probed(config, features=features, budget=budget)
    except Exception:
        logger.warning(
            "Fast-model availability check failed; "
            "utility features use the active model until it succeeds.",
            exc_info=True,
        )


async def _ensure_probed(
    config: VibeConfigSchema, *, features: tuple[UtilityFeature, ...], budget: float
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
        provider=provider, models=candidates, timeout_seconds=budget
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
) -> str | None:
    """Run a single non-streaming completion for a background nicety.

    Owns model selection, backend construction, budgets, user-agent and the
    request metadata so features don't re-implement the seam. Returns the raw
    message content; the caller decides how to clean and interpret it.

    ``feature`` picks the model and the telemetry label; calls without one
    (worktree naming) pass ``call_type``. ``launch_context`` and ``session_id``
    give it the same attribution the session's own calls carry. A telemetry
    client, when given, also emits one ``vibe.request_sent`` event for the call.

    ``skip_if_no_key`` returns None before any network setup when the selected
    provider declares an API-key env var that is unset. It's for callers on a
    latency-sensitive path with a ready fallback (e.g. worktree naming at
    startup); leave it False so a missing key surfaces as a real backend failure
    instead of a silent no-op. A keyless local provider (empty ``api_key_env_var``,
    e.g. llama-server) is never skipped, since it needs no key to reach.
    """
    call_type = _call_type(feature, call_type)
    model, provider = select_utility_model(config, feature=feature)
    if (
        skip_if_no_key
        and provider.api_key_env_var
        and not resolve_api_key(provider.api_key_env_var)
    ):
        return None
    if telemetry is not None:
        telemetry.send_request_sent(
            model=model.alias,
            nb_context_chars=len(system_prompt) + len(user_content),
            nb_context_messages=2,
            nb_prompt_chars=len(user_content),
            call_type=call_type,
        )
    backend = create_backend(
        provider=provider,
        timeout=request_timeout_seconds,
        retry_max_elapsed_time=retry_budget_seconds,
    )
    async with backend:
        result = await backend.complete(
            model=model,
            messages=[
                LLMMessage(role=Role.system, content=system_prompt),
                LLMMessage(role=Role.user, content=user_content),
            ],
            temperature=0.0,
            tools=None,
            tool_choice=None,
            max_tokens=max_tokens,
            extra_headers={"user-agent": get_user_agent(provider.backend)},
            metadata=build_request_metadata(
                launch_context=launch_context,
                session_id=session_id,
                call_type=call_type,
            ).model_dump(exclude_none=True),
        )
    return result.message.content


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
    config: VibeConfigSchema,
) -> tuple[ModelConfig, ProviderConfig] | None:
    """The first fast candidate the deployment serves; keyless providers qualify."""
    provider = config.get_mistral_provider()
    if provider is None:
        return None
    if provider.api_key_env_var and not resolve_api_key(provider.api_key_env_var):
        return None
    assume_first = _is_public_mistral_api(provider.api_base)
    for index, candidate in enumerate(FAST_MODEL_CANDIDATES):
        if not _fast_model_allowed(config, candidate):
            continue
        known = MODEL_AVAILABILITY.peek(provider=provider, model=candidate)
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
