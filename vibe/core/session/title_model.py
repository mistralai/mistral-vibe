from __future__ import annotations

import asyncio
from collections.abc import Sequence
import re

from vibe.core.config import VibeConfigSchema
from vibe.core.llm.utility_completion import run_utility_completion
from vibe.core.prompts import UtilityPrompt
from vibe.core.session.title_policy import DEFAULT_TITLE_POLICY, TitlePolicy
from vibe.core.types import LLMMessage, Role

_ELISION = "\n\n[…]\n\n"
_WHITESPACE_RE = re.compile(r"\s+")
# Strip control chars: the title is written raw into a terminal OSC sequence.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WRAPPING_QUOTES = "\"'`“”‘’"


async def generate_session_title(
    messages: Sequence[LLMMessage],
    *,
    config: VibeConfigSchema,
    previous_title: str | None = None,
    policy: TitlePolicy = DEFAULT_TITLE_POLICY,
) -> str | None:
    """Ask a model for a concise title describing the session.

    ``previous_title`` is fed back so the model can refine an earlier title
    rather than start over. Returns None when there is nothing to title (empty
    transcript) or the model gives no usable answer. Raises on any real failure
    (misconfiguration, backend error, timeout) so the caller can log it and we
    fix the cause; the single background boundary in the agent loop keeps such a
    failure from disrupting the session.
    """
    transcript = build_title_transcript(messages, policy=policy)
    if not transcript:
        return None
    async with asyncio.timeout(policy.total_timeout_seconds):
        content = await run_utility_completion(
            config=config,
            system_prompt=UtilityPrompt.SESSION_TITLE.read(),
            user_content=_user_prompt(transcript, previous_title),
            max_tokens=policy.max_tokens,
            request_timeout_seconds=policy.request_timeout_seconds,
            retry_budget_seconds=policy.retry_budget_seconds,
        )
    return _clean_title(content, policy=policy)


def build_title_transcript(
    messages: Sequence[LLMMessage], *, policy: TitlePolicy = DEFAULT_TITLE_POLICY
) -> str:
    blocks: list[str] = []
    for message in messages:
        if message.role == Role.system:
            continue
        text = (message.content or "").strip()
        if not text:
            continue
        if len(text) > policy.max_message_chars:
            text = text[: policy.max_message_chars]
        blocks.append(f"{message.role.value}: {text}")
    transcript = "\n\n".join(blocks).strip()
    if len(transcript) <= policy.max_transcript_chars:
        return transcript
    head = transcript[: policy.head_transcript_chars].rstrip()
    tail = transcript[-policy.tail_transcript_chars :].lstrip()
    return f"{head}{_ELISION}{tail}"


def _user_prompt(transcript: str, previous_title: str | None) -> str:
    if not previous_title:
        return transcript
    return f"Current title: {previous_title}\n\nTranscript:\n{transcript}"


def _clean_title(
    content: str | None, *, policy: TitlePolicy = DEFAULT_TITLE_POLICY
) -> str | None:
    if not content:
        return None
    stripped = content.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    first_line = _CONTROL_CHARS_RE.sub("", first_line)
    collapsed = _WHITESPACE_RE.sub(" ", first_line).strip().strip(_WRAPPING_QUOTES)
    collapsed = collapsed.strip()
    if not collapsed or collapsed.lower() in policy.generic_titles:
        return None
    if len(collapsed) > policy.max_title_chars:
        collapsed = collapsed[: policy.max_title_chars].rstrip() + "…"
    return collapsed
