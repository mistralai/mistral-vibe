from __future__ import annotations

from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.llm.backend.factory import create_backend
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.types import LLMMessage, Role
from vibe.core.utils.matching import name_matches
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import get_user_agent

# A small, fast model preferred for background niceties (titles, worktree names):
# latency and cost matter more than reasoning depth.
_FAST_MODEL = ModelConfig(
    name="mistral-vibe-cli-fast",
    provider="mistral",
    alias="mistral-small",
    input_price=0.1,
    output_price=0.3,
)


def select_utility_model(
    config: VibeConfigSchema,
) -> tuple[ModelConfig, ProviderConfig]:
    """Pick the model for a secondary/utility completion.

    Anchored to the session's active model/provider so a utility call never
    reaches a destination the session isn't already using. The small fast
    Mistral model is substituted only when the active provider is already
    Mistral and the allowlist permits it.
    """
    active = config.get_active_model()
    provider = config.get_provider_for_model(active)
    if provider.name == _FAST_MODEL.provider and _fast_model_allowed(config):
        return _FAST_MODEL, provider
    return active, provider


def is_fast_utility_model(config: VibeConfigSchema) -> bool:
    """Whether the utility model resolves to the cheap fast model.

    False means it fell back to the session's active model, which may be large
    and expensive, so callers can throttle background use accordingly.
    """
    model, _ = select_utility_model(config)
    return model.name == _FAST_MODEL.name


def _fast_model_allowed(config: VibeConfigSchema) -> bool:
    return not config.allowed_models or name_matches(
        _FAST_MODEL.alias, config.allowed_models
    )


async def run_utility_completion(
    *,
    config: VibeConfigSchema,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    request_timeout_seconds: float,
    retry_budget_seconds: float,
    skip_if_no_key: bool = False,
) -> str | None:
    """Run a single non-streaming completion for a background nicety.

    Owns model selection, backend construction, budgets, user-agent and the
    ``secondary_call`` metadata so features don't re-implement the seam. Returns
    the raw message content; the caller decides how to clean and interpret it.

    ``skip_if_no_key`` returns None before any network setup when the selected
    provider declares an API-key env var that is unset. It's for callers on a
    latency-sensitive path with a ready fallback (e.g. worktree naming at
    startup); leave it False so a missing key surfaces as a real backend failure
    instead of a silent no-op. A keyless local provider (empty ``api_key_env_var``,
    e.g. llama-server) is never skipped, since it needs no key to reach.
    """
    model, provider = select_utility_model(config)
    if (
        skip_if_no_key
        and provider.api_key_env_var
        and not resolve_api_key(provider.api_key_env_var)
    ):
        return None
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
                launch_context=None, session_id=None, call_type="secondary_call"
            ).model_dump(exclude_none=True),
        )
    return result.message.content
