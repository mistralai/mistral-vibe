from __future__ import annotations

import asyncio
from pathlib import Path

from vibe.core.config import ModelConfig
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.llm.backend.factory import create_backend
from vibe.core.prompts import UtilityPrompt
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.types import Backend, LLMMessage, Role
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import get_user_agent

_NAMING_MODEL = ModelConfig(
    name="mistral-vibe-cli-fast",
    provider="mistral",
    alias="mistral-small",
    input_price=0.1,
    output_price=0.3,
)

# The caller has a deterministic name ready, so waiting is worth less than
# starting. The backend deadline bounds one attempt and the outer timeout bounds
# everything around it, including config loading and connection setup.
_REQUEST_TIMEOUT_SECONDS = 1.5
_TOTAL_TIMEOUT_SECONDS = 2.0
_MAX_TOKENS = 24


async def suggest_worktree_name(prompt: str | None, *, cwd: Path) -> str | None:
    """Ask a small model to name a worktree after the session's first message.

    Returns None whenever a name cannot be produced -- no prompt, no provider,
    no API key, too slow, or any failure at all. Naming is a nicety and the
    caller always holds a deterministic fallback, so this must never be the
    reason a session fails to start.
    """
    if not prompt:
        return None
    try:
        async with asyncio.timeout(_TOTAL_TIMEOUT_SECONDS):
            return await _complete(prompt, cwd=cwd)
    except TimeoutError:
        logger.debug("Worktree name suggestion timed out")
        return None
    except Exception as exc:
        logger.warning("Worktree name suggestion failed", exc_info=exc)
        return None


async def _complete(prompt: str, *, cwd: Path) -> str | None:
    # Scoped to the session cwd so a trusted project config is read from the
    # repo being worked in, matching HostRequestHandler._load_orchestrator.
    # Reads the global manager rather than building one: loading config resolves
    # prompts through the global singleton, so callers have to have initialised
    # it regardless and a private instance here would only mask that.
    orchestrator = await build_default_orchestrator(
        harness_files=get_harness_files_manager().for_session(cwd),
        require_api_key=False,
    )
    provider = next(
        (
            candidate
            for candidate in orchestrator.config.providers
            if candidate.name == _NAMING_MODEL.provider
        ),
        None,
    )
    # Logged because the fallback name is indistinguishable from a working one:
    # without this the only symptom of a misconfigured VIBE_HOME is worse names.
    if provider is None:
        logger.debug(
            "No %s provider; naming the worktree locally", _NAMING_MODEL.provider
        )
        return None
    if provider.api_key_env_var and not resolve_api_key(provider.api_key_env_var):
        logger.debug("%s unset; naming the worktree locally", provider.api_key_env_var)
        return None

    backend = create_backend(
        provider=provider,
        timeout=_REQUEST_TIMEOUT_SECONDS,
        # A retry cannot fit inside the budget, and the fallback name is already
        # good enough to not be worth spending one on.
        retry_max_elapsed_time=0,
    )
    async with backend:
        result = await backend.complete(
            model=_NAMING_MODEL,
            messages=[
                LLMMessage(
                    role=Role.system, content=UtilityPrompt.WORKTREE_NAME.read()
                ),
                LLMMessage(role=Role.user, content=prompt),
            ],
            temperature=0.0,
            tools=None,
            tool_choice=None,
            max_tokens=_MAX_TOKENS,
            extra_headers={"user-agent": get_user_agent(Backend.MISTRAL)},
            metadata=build_request_metadata(
                launch_context=None, session_id=None, call_type="secondary_call"
            ).model_dump(exclude_none=True),
        )
    return result.message.content or None
