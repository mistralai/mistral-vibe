from __future__ import annotations

import asyncio
from pathlib import Path

from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.llm.utility_completion import run_utility_completion
from vibe.core.prompts import UtilityPrompt
from vibe.observability.logging import logger

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
    content = await run_utility_completion(
        config=orchestrator.config,
        system_prompt=UtilityPrompt.WORKTREE_NAME.read(),
        user_content=prompt,
        max_tokens=_MAX_TOKENS,
        request_timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        # A retry cannot fit inside the budget, and the fallback name is already
        # good enough to not be worth spending one on.
        retry_budget_seconds=0,
        # Naming runs on the session-start path with a deterministic name ready;
        # a keyless provider should fall back at once instead of burning the
        # budget on setup that can only fail.
        skip_if_no_key=True,
    )
    return content or None
