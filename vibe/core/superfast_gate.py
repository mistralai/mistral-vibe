"""Superfast Decision Gate -- System One front-door classifier (shadow mode).

A small, non-autoregressive "System One" decision model (Von, or any
Jev-compatible server) evaluates the pending user turn in a single forward
pass and returns typed, calibrated answers without generating text. The
Decision Gate turns those answers into a conservative routing
recommendation so a harness could skip expensive System Two work when the
decision is obvious.

This is the first, safe increment. The contract is:
  - Off by default. Nothing runs unless ``SUPERFAST_ENABLED`` is set.
  - Shadow mode. The gate only classifies and logs the recommended route and
    latency through the project logger. It never changes routing, never skips
    the model call, and never alters user-visible behaviour.
  - Fail open. Any error, timeout, non-2xx response, unreachable backend, or
    malformed body yields "no opinion" and the agent continues unchanged.
  - No heavy new dependency. It talks to a local HTTP endpoint with the
    httpx client the project already uses. The decision model is installed
    out of band, not bundled.

Concept and reference implementation by Andrea Bruno, released under the
Creative Commons Attribution 4.0 (CC BY 4.0) licence. If this idea or code
is adopted, please keep a credit to Andrea Bruno and a link to the
harness-superfast repository. The decision models themselves (Von, OpenJev,
Laya) are third-party open models; only the integration architecture and the
routing method are ours.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any

import httpx

from vibe.observability.logging import logger

__all__ = ["classify_turn", "shadow_log_turn"]

# Default wire settings for the Jev-compatible decision backend.
DEFAULT_ENDPOINT = "http://localhost:8000/v1/systemone"
DEFAULT_MODEL = "von-1.2.0"
DEFAULT_TIMEOUT_MS = 150

# Minimum calibrated intent confidence required for the plain_chat fast route.
PLAIN_CHAT_CONFIDENCE_FLOOR = 0.5

# Conservative thresholds for the derived route. A fast route is only
# recommended when the relevant probabilities are clearly decisive.
DECISIVE_NOUL = 0.85
LOW_TOOL_NEED = 0.3
VERY_LOW_TOOL_NEED = 0.2

# The three typed questions asked in a single forward pass. Kept small so the
# request stays well under the timeout budget.
_TURN_QUESTIONS: dict[str, dict[str, Any]] = {
    "needs_tool": {
        "type": "noul",
        "instructions": (
            "Does answering this request require taking an action with a tool "
            "(reading, writing, running, searching), rather than replying from "
            "what is already known?"
        ),
    },
    "answerable_from_context": {
        "type": "noul",
        "instructions": (
            "Can this request be answered from information already present in "
            "the conversation, without any new investigation?"
        ),
    },
    "intent": {
        "type": "choice",
        "instructions": "Classify the primary intent of the user request.",
        "criteria": {
            "code_change": "Create, edit, or delete code or files.",
            "code_question": "Explain or reason about code without changing it.",
            "command": "Run a command or operation.",
            "chat": "Casual conversation or a question needing no tools.",
            "other": "None of the above.",
        },
    },
}

# Holds strong references to in-flight shadow tasks so the event loop does not
# garbage-collect them before they finish.
_pending_tasks: set[asyncio.Task[None]] = set()


def _is_enabled() -> bool:
    """Return True only when SUPERFAST_ENABLED is explicitly turned on."""
    return os.environ.get("SUPERFAST_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _endpoint() -> str:
    return os.environ.get("SUPERFAST_ENDPOINT") or DEFAULT_ENDPOINT


def _model() -> str:
    return os.environ.get("SUPERFAST_MODEL") or DEFAULT_MODEL


def _timeout_ms() -> int:
    raw = os.environ.get("SUPERFAST_TIMEOUT_MS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_TIMEOUT_MS


def _read_noul(answer: object) -> float | None:
    """Return a noul probability only when it is a finite value in [0, 1].

    Anything else (absent, NaN, Infinity, out of range, wrong type, bool) is
    treated as "no evidence" so a mis-scaled answer can never look decisive.
    """
    value = answer.get("noul") if isinstance(answer, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _confident(answer: object, floor: float) -> bool:
    """Return True only for a finite confidence in [0, 1] at or above *floor*."""
    value = answer.get("confidence") if isinstance(answer, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return math.isfinite(number) and 0.0 <= number <= 1.0 and number >= floor


def _derive_route(answers: dict[str, Any]) -> str:
    """Derive a conservative route; only a decisive signal yields a fast route."""
    needs_tool = _read_noul(answers.get("needs_tool"))
    from_context = _read_noul(answers.get("answerable_from_context"))

    # A decisive "needs a tool" wins first -- the harness must not skip work.
    if needs_tool is not None and needs_tool >= DECISIVE_NOUL:
        return "needs_tool"

    # Strongly answerable from context, with a present and low tool-need signal.
    if (
        from_context is not None
        and from_context >= DECISIVE_NOUL
        and needs_tool is not None
        and needs_tool <= LOW_TOOL_NEED
    ):
        return "answer_from_context"

    # Clearly chat, with a calibrated intent and a present, low tool-need signal.
    intent = answers.get("intent")
    if (
        isinstance(intent, dict)
        and intent.get("choice") == "chat"
        and _confident(intent, PLAIN_CHAT_CONFIDENCE_FLOOR)
        and needs_tool is not None
        and needs_tool <= VERY_LOW_TOOL_NEED
    ):
        return "plain_chat"

    return "unknown"


async def _query_system_one(
    state: str, questions: dict[str, Any], endpoint: str, model: str, timeout_ms: int
) -> dict[str, Any] | None:
    """POST one System One request and return the answers dict.

    Returns ``None`` on any failure (fail-open). Never raises for a normal
    transport, status, or parse failure.
    """
    try:
        # trust_env=False keeps the local decision request off any configured
        # proxy so a loopback backend is always reachable directly.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_ms / 1000.0), trust_env=False
        ) as client:
            response = await client.post(
                endpoint, json={"model": model, "state": state, "questions": questions}
            )
        if not response.is_success:
            return None
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    answers = payload.get("answers")
    return answers if isinstance(answers, dict) else None


async def classify_turn(user_message: str) -> tuple[str, int] | None:
    """Classify a user turn through the gate.

    Returns ``(route, latency_ms)`` when the gate produced an opinion, or
    ``None`` when it is disabled or unavailable (fail-open).
    """
    if not _is_enabled():
        return None
    started = time.perf_counter()
    answers = await _query_system_one(
        user_message, _TURN_QUESTIONS, _endpoint(), _model(), _timeout_ms()
    )
    if answers is None:
        return None
    route = _derive_route(answers)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return route, latency_ms


def shadow_log_turn(user_message: str) -> None:
    """Fire-and-forget shadow classification for the current user turn.

    Spawns a background task that logs the gate's recommended route and
    latency, then returns immediately so the real turn sees no added latency.
    Does nothing when the gate is disabled. Never raises into the caller.
    """
    if not _is_enabled():
        return
    task = asyncio.create_task(_shadow(user_message))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


async def _shadow(user_message: str) -> None:
    try:
        result = await classify_turn(user_message)
    except Exception as exc:
        logger.debug("Superfast gate shadow error (fail-open) cause=%s", exc)
        return
    if result is None:
        return
    route, latency_ms = result
    logger.info("Superfast gate shadow route=%s latency_ms=%d", route, latency_ms)
