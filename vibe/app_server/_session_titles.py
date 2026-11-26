"""When a Session gets a title, what the title model reads, and how its reply is cleaned.

Pure logic over Vibe's public history: the caller feeds the cadence the entries
and Turn ends it hears, asks it whether a title call is due, and runs the call
through ``generate_session_title`` with its own completion function. Writing the
title, and whether the user renamed the Session, stay with the caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
import dataclasses
import json
import re

from pydantic import BaseModel, JsonValue

from vibe.app_server.models import (
    DiscardedGenerationOutcome,
    PendingEffectState,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    SkippedEffectState,
    TextContentBlock,
)
from vibe.observability.logging import logger

_ELISION = "\n\n[…]\n\n"
_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WRAPPING_QUOTES = "\"'`“”‘’"
# The outcome public history gives a user message a hook blocked.
_SKIPPED_CAUSE = "skipped"


@dataclasses.dataclass(frozen=True, slots=True)
class TitlePolicy:
    confirm_after_steps: int = 6
    capped_max_generations: int = 2
    initial_max_steps: int = 3
    max_transcript_chars: int = 6000
    head_transcript_chars: int = 1500
    max_message_chars: int = 2000
    max_tool_chars: int = 400
    request_timeout_seconds: float = 6.0
    retry_budget_seconds: float = 10.0
    total_timeout_seconds: float = 20.0
    # Covers a reasoning trace too: a model that cannot turn reasoning off
    # spends output tokens on it before the title.
    max_tokens: int = 1024
    max_title_chars: int = 72
    generic_titles: frozenset[str] = frozenset({
        "new session",
        "untitled session",
        "untitled",
    })

    @property
    def tail_transcript_chars(self) -> int:
        return self.max_transcript_chars - self.head_transcript_chars


DEFAULT_TITLE_POLICY = TitlePolicy()


@dataclasses.dataclass(frozen=True, slots=True)
class TitleRequest:
    """One title call: what the model reads after the title prompt, and its limits."""

    user_content: str
    max_tokens: int
    request_timeout_seconds: float
    retry_budget_seconds: float


# Runs one title call and returns the model's raw reply.
type TitleCompletion = Callable[[TitleRequest], Awaitable[str | None]]


async def generate_session_title(
    entries: Sequence[PublicHistoryEntry],
    *,
    previous_title: str | None,
    complete: TitleCompletion,
    policy: TitlePolicy = DEFAULT_TITLE_POLICY,
) -> str | None:
    """The cleaned title for ``entries``, or None when there is none to give.

    None when the transcript is empty, the call fails or runs past the policy's
    total limit, or the reply is not a usable title. A failure is logged: a
    title is a background nicety, so it never raises.
    """
    transcript = build_title_transcript(entries, policy=policy)
    if not transcript:
        return None
    request = TitleRequest(
        user_content=_user_prompt(transcript, previous_title),
        max_tokens=policy.max_tokens,
        request_timeout_seconds=policy.request_timeout_seconds,
        retry_budget_seconds=policy.retry_budget_seconds,
    )
    try:
        async with asyncio.timeout(policy.total_timeout_seconds):
            content = await complete(request)
    except TimeoutError:
        logger.warning("Title completion timed out")
        return None
    except Exception:
        logger.warning("Title completion failed", exc_info=True)
        return None
    return clean_title(content, policy=policy)


class TitleCadence:
    """When a Session's title call is due.

    A model step is a completed assistant message, or a completed tool call with
    some output, counted once per entry ID. The first title is due at a Turn
    end, at ``initial_max_steps`` steps, or as soon as a compaction lands. One
    confirmation is due ``confirm_after_steps`` steps after the last attempt.
    An attempt counts when it starts, whatever its outcome, up to
    ``capped_max_generations``. A title this cadence did not generate, such as
    a rename or the title a Session reopened with, stops it for good: a
    reopened titled Session is never regenerated, whatever its title source.
    """

    def __init__(
        self,
        entries: Iterable[PublicHistoryEntry] = (),
        *,
        policy: TitlePolicy = DEFAULT_TITLE_POLICY,
    ) -> None:
        self._policy = policy
        self._step_ids: set[str] = set()
        self._compaction_landed = False
        self._generated_title: str | None = None
        self._attempts = 0
        self._last_attempt_step: int | None = None
        self.reset(entries)

    @property
    def steps(self) -> int:
        return len(self._step_ids)

    def observe(self, entry: PublicHistoryEntry) -> None:
        """Count a new or updated history entry."""
        if _is_model_step(entry):
            self._step_ids.add(entry.id)
        if _is_landed_compaction(entry):
            self._compaction_landed = True

    def reset(self, entries: Iterable[PublicHistoryEntry]) -> None:
        """Count steps again from ``entries``, as after a rewind.

        Attempts and the generated title are kept. Steps a rewind removed no
        longer separate the last attempt from the next one.
        """
        self._step_ids = set()
        self._compaction_landed = False
        for entry in entries:
            self.observe(entry)
        if self._last_attempt_step is not None:
            self._last_attempt_step = min(self._last_attempt_step, self.steps)

    def is_manual(self, current_title: str | None) -> bool:
        return current_title is not None and current_title != self._generated_title

    def begin_if_due(
        self, *, current_title: str | None, turn_ended: bool = False
    ) -> bool:
        """Whether a title call is due now; True also counts the attempt."""
        if self.is_manual(current_title):
            return False
        if self._attempts >= self._policy.capped_max_generations:
            return False
        step = self.steps
        if self._last_attempt_step is None:
            due = (
                step >= self._policy.initial_max_steps
                or turn_ended
                or self._compaction_landed
            )
        else:
            due = step - self._last_attempt_step >= self._policy.confirm_after_steps
        if not due:
            return False
        self._attempts += 1
        self._last_attempt_step = step
        return True

    def record(self, *, title: str) -> None:
        """Remember the title an attempt wrote, so it does not read as a rename."""
        self._generated_title = title


def build_title_transcript(
    entries: Sequence[PublicHistoryEntry], *, policy: TitlePolicy = DEFAULT_TITLE_POLICY
) -> str:
    """The transcript the title model reads.

    User and assistant messages read as public history shows them; a user
    message a hook blocked is left out, as the agent's model never saw it.
    Each tool call reads as its input and its result or error.
    """
    blocks: list[str] = []
    for entry in entries:
        match entry:
            case PublicMessageEntry():
                if _is_skipped_user_message(entry):
                    continue
                block = _message_block(entry, policy=policy)
            case PublicEffectEntry():
                block = _tool_block(entry, policy=policy)
            case _:
                continue
        if block:
            blocks.append(block)
    transcript = "\n\n".join(blocks).strip()
    if len(transcript) <= policy.max_transcript_chars:
        return transcript
    head = transcript[: policy.head_transcript_chars].rstrip()
    tail = transcript[-policy.tail_transcript_chars :].lstrip()
    return f"{head}{_ELISION}{tail}"


def clean_title(
    content: str | None, *, policy: TitlePolicy = DEFAULT_TITLE_POLICY
) -> str | None:
    if not content:
        return None
    stripped = content.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    first_line = _CONTROL_CHARS_RE.sub("", first_line)
    collapsed = (
        _WHITESPACE_RE.sub(" ", first_line).strip().strip(_WRAPPING_QUOTES).strip()
    )
    if not collapsed or collapsed.lower() in policy.generic_titles:
        return None
    if len(collapsed) > policy.max_title_chars:
        collapsed = collapsed[: policy.max_title_chars].rstrip() + "…"
    return collapsed


def _user_prompt(transcript: str, previous_title: str | None) -> str:
    if not previous_title:
        return transcript
    return f"Current title: {previous_title}\n\nTranscript:\n{transcript}"


def _is_model_step(entry: PublicHistoryEntry) -> bool:
    if entry.generation_status is not PublicEntryGenerationStatus.COMPLETED:
        return False
    match entry:
        case PublicMessageEntry(role="assistant"):
            return True
        case PublicEffectEntry():
            return bool(_output_text(entry).strip())
        case _:
            return False


def _is_landed_compaction(entry: PublicHistoryEntry) -> bool:
    return (
        isinstance(entry, PublicCheckpointEntry)
        and entry.kind == "compaction"
        and entry.generation_status is not PublicEntryGenerationStatus.IN_PROGRESS
    )


def _is_skipped_user_message(entry: PublicMessageEntry) -> bool:
    return (
        entry.role == "user"
        and isinstance(entry.outcome, DiscardedGenerationOutcome)
        and entry.outcome.cause == _SKIPPED_CAUSE
    )


def _message_block(entry: PublicMessageEntry, *, policy: TitlePolicy) -> str | None:
    if entry.role not in {"user", "assistant"}:
        return None
    text = "\n".join(
        block.text for block in entry.content if isinstance(block, TextContentBlock)
    ).strip()
    if not text:
        return None
    return f"{entry.role}: {text[: policy.max_message_chars]}"


def _tool_block(entry: PublicEffectEntry, *, policy: TitlePolicy) -> str | None:
    name = entry.detail.tool_name or entry.title or "tool"
    lines: list[str] = []
    call = _tool_input(entry)
    if call is not None:
        call_text = (
            call
            if isinstance(call, str)
            else json.dumps(call, ensure_ascii=False, default=str)
        ).strip()
        if call_text and call_text not in {"{}", "[]", '""'}:
            lines.append(f"tool({name}): {call_text[: policy.max_tool_chars]}")
    output = _output_text(entry).strip()
    if output:
        lines.append(f"tool result({name}): {output[: policy.max_tool_chars]}")
    elif entry.state.status == "failed":
        message = entry.state.error.message.strip()
        if message:
            lines.append(
                f"tool result({name}) failed: {message[: policy.max_tool_chars]}"
            )
    return "\n".join(lines) if lines else None


def _tool_input(entry: PublicEffectEntry) -> JsonValue:
    """The call's arguments as JSON, leaving out the fields the call did not set."""
    value = entry.detail.input
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _output_text(entry: PublicEffectEntry) -> str:
    state = entry.state
    if isinstance(state, PendingEffectState | SkippedEffectState):
        return ""
    return state.output_text


__all__ = [
    "DEFAULT_TITLE_POLICY",
    "TitleCadence",
    "TitleCompletion",
    "TitlePolicy",
    "TitleRequest",
    "build_title_transcript",
    "clean_title",
    "generate_session_title",
]
