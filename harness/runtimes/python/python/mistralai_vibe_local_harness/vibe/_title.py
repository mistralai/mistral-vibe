"""Background session-title generation for the Unified Harness runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
import dataclasses
import logging
import re

from pydantic import JsonValue

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustMessage,
    RustSystemMessage,
    RustTextContentBlock,
    RustUserMessage,
)
from mistralai_vibe_local_harness.session_protocol import JsonObject
from mistralai_vibe_local_harness.vibe._completion import (
    _REJECTION_REASONS,
    _provider_status,
)
from mistralai_vibe_local_harness.vibe._credentials import (
    ProviderAuthRequired,
    ProviderCredentialResult,
    ProviderCredentialSnapshot,
)
from mistralai_vibe_local_harness.vibe._runtime_config import (
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
)
from mistralai_vibe_local_harness.vibe.adapters.generic import (
    execute_generic_completion,
)
from mistralai_vibe_local_harness.vibe.adapters.mistral import (
    execute_mistral_completion,
)

logger = logging.getLogger(__name__)

_SESSION_TITLE_SYSTEM_PROMPT = """\
You write short, descriptive titles for coding-agent sessions. Given a transcript of a session between a user and an AI coding assistant, reply with a concise title naming what the session is about.

Rules:
- 3 to 8 words. No trailing period.
- Name the task or topic, not the request. Describe what is being worked on, not that the user asked for it.
- Base the title on what the conversation reveals the task to be, including facts the assistant uncovered (for example, the real subject of a linked issue or ticket). If the user's opening message is just a link or a terse reference, title what it turned out to be about, not the reference itself.
- Ignore the assistant's process narration and preambles ("I'll explore…", "Let me…", "First, I'll…", "I'll start by…"); these describe activity, not the task.
- Prefer specific nouns from the code or domain over generic phrases. "Fix Stripe webhook retries" beats "Fix a bug".
- Plain text only, in sentence case. No quotes, backticks, markdown, code fences, or emoji.
- Always answer in English. If the transcript is in another language, translate the intent rather than transliterating.
- Prefer the shortest title that still captures the topic.
- If a `Current title:` is given, keep it unless the session's focus has clearly shifted, in which case refine it.
- If the transcript is empty or describes no task, answer `New session`.

Respond with ONLY the title, on one line, with no quotes or explanation.
"""

_ELISION = "\n\n[…]\n\n"
_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WRAPPING_QUOTES = "\"'`“”‘’"


@dataclasses.dataclass(frozen=True, slots=True)
class TitlePolicy:
    refresh_every_steps: int = 6
    capped_max_generations: int = 2
    # How many model steps into the first turn the initial title fires (or at the
    # first turn's completion, whichever comes first). Kept low so a long,
    # tool-heavy single turn — which emits few assistant messages — still gets a
    # title while it runs, not only when it ends or is interrupted. Title quality
    # comes from the fast model and the prompt, not from waiting; the periodic
    # refresh then sharpens it as the work proceeds. ``count_model_steps`` counts
    # tool activity too, so "3 steps" is reached by real work, not just chatter.
    initial_max_steps: int = 3
    max_transcript_chars: int = 6000
    head_transcript_chars: int = 1500
    max_message_chars: int = 2000
    request_timeout_seconds: float = 6.0
    retry_budget_seconds: float = 10.0
    total_timeout_seconds: float = 20.0
    max_tokens: int = 96
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


def build_title_transcript(
    entries: Sequence[JsonObject], *, policy: TitlePolicy = DEFAULT_TITLE_POLICY
) -> str:
    blocks: list[str] = []
    for entry in entries:
        if entry.get("type") != "message":
            continue
        role = entry.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _entry_text(entry).strip()
        if not text:
            continue
        if len(text) > policy.max_message_chars:
            text = text[: policy.max_message_chars]
        blocks.append(f"{role}: {text}")
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


def _cold_route(config: LocalRuntimeAdapterConfig) -> LocalModelRoute:
    return LocalModelRoute(
        model=config.active_model.model, temperature=0.0, thinking="off"
    )


def _title_completion_config(
    config: LocalRuntimeAdapterConfig, *, policy: TitlePolicy
) -> LocalRuntimeAdapterConfig:
    """Config the title completion runs against: the session's, unless a title
    provider route redirects it to another provider.

    The completion adapters take a single ``LocalRuntimeAdapterConfig``, so a
    cross-provider title (e.g. the fast Mistral model while the session runs on
    Anthropic) is expressed by overlaying the provider destination fields and the
    credential port. With no route set the title stays on the session provider.

    The session's telemetry sinks are always dropped: a background title is not
    the user's turn, so its provider correlation id and request metrics must not
    be written into the session's holders (a later rating would otherwise join
    the title call instead of the turn).
    """
    overlaid = dataclasses.replace(
        config,
        max_tokens=policy.max_tokens,
        correlation_id_sink=None,
        request_sent_sink=None,
    )
    route = config.title_provider
    if route is None:
        return overlaid
    return dataclasses.replace(
        overlaid,
        provider=route.provider,
        backend=route.backend,
        api_style=route.api_style,
        base_url=route.base_url,
        credentials=route.credentials,
        reasoning_field_name=route.reasoning_field_name,
        emits_finish_reason=route.emits_finish_reason,
        extra_headers=dict(route.extra_headers),
        project_id=route.project_id,
        region=route.region,
    )


def _user_prompt(transcript: str, previous_title: str | None) -> str:
    if not previous_title:
        return transcript
    return f"Current title: {previous_title}\n\nTranscript:\n{transcript}"


async def execute_title_completion(
    *,
    transcript: str,
    config: LocalRuntimeAdapterConfig,
    policy: TitlePolicy = DEFAULT_TITLE_POLICY,
    previous_title: str | None = None,
) -> str | None:
    title_config = _title_completion_config(config, policy=policy)
    credential: ProviderCredentialResult = await title_config.credentials.resolve()
    if isinstance(credential, ProviderAuthRequired):
        return None
    assert isinstance(credential, ProviderCredentialSnapshot)

    route = config.title_model or _cold_route(config)
    messages: list[RustMessage] = [
        RustSystemMessage(
            content=[RustTextContentBlock(text=_SESSION_TITLE_SYSTEM_PROMPT)]
        ),
        RustUserMessage(
            content=[
                RustTextContentBlock(text=_user_prompt(transcript, previous_title))
            ]
        ),
    ]
    try:
        result: RustCompletionResult
        if title_config.backend == "mistral":
            result = await execute_mistral_completion(
                messages,
                tools=[],
                config=title_config,
                credential=credential,
                route=route,
                stream=False,
            )
        elif title_config.backend == "generic":
            result = await execute_generic_completion(
                messages,
                tools=[],
                config=title_config,
                credential=credential,
                route=route,
            )
        else:
            raise ValueError(f"Unsupported completion backend: {title_config.backend}")
    except Exception as exc:
        # Report a refused credential only when the title ran on its own
        # credential port (a cross-provider route), so the same key is not
        # re-sent on every subsequent title tick. A same-provider title shares
        # the session's port, and a title-only refusal (e.g. the fast model
        # forbidden on an otherwise valid key) must not mark the session
        # credential rejected and break the next turn. The reject is itself
        # guarded: a raising port must not turn a dropped title into a failure
        # that escapes this background task.
        rejection = _REJECTION_REASONS.get(_provider_status(exc) or 0)
        if rejection is not None and config.title_provider is not None:
            with suppress(Exception):
                await title_config.credentials.reject(
                    observed_revision=credential.revision, reason=rejection
                )
        logger.warning("Title completion failed", exc_info=True)
        return None
    if not result.parts:
        return None
    return getattr(result.parts[0], "text", None) or None


async def generate_session_title(
    entries: Sequence[JsonObject],
    *,
    config: LocalRuntimeAdapterConfig,
    policy: TitlePolicy = DEFAULT_TITLE_POLICY,
    previous_title: str | None = None,
) -> str | None:
    transcript = build_title_transcript(entries, policy=policy)
    if not transcript:
        return None
    try:
        async with asyncio.timeout(policy.total_timeout_seconds):
            content = await execute_title_completion(
                transcript=transcript,
                config=config,
                policy=policy,
                previous_title=previous_title,
            )
    except TimeoutError:
        logger.warning("Title completion timed out")
        return None
    return clean_title(content, policy=policy)


def count_model_steps(entries: Sequence[JsonValue]) -> int:
    """Count model progress for the title cadence: assistant messages plus effects.

    A single long turn can be almost entirely tool calls with only a handful of
    assistant messages. Counting messages alone would leave such a turn's title
    frozen until it ends. Effects (tool calls, background processes) are the
    public projection's record of that work, so counting them too lets the
    cadence advance — and the periodic refresh fire — while the turn runs.
    """
    return sum(
        1
        for entry in entries
        if isinstance(entry, dict)
        and (
            (entry.get("type") == "message" and entry.get("role") == "assistant")
            or entry.get("type") == "effect"
        )
    )


def latest_compaction_id(entries: Sequence[JsonValue]) -> str | None:
    """Id of the most recent completed compaction checkpoint, or None."""
    for entry in reversed(entries):
        if (
            isinstance(entry, dict)
            and entry.get("type") == "checkpoint"
            and entry.get("kind") == "compaction"
            and entry.get("generationStatus") != "in_progress"
        ):
            entry_id = entry.get("id")
            return entry_id if isinstance(entry_id, str) else None
    return None


class TitleCadence:
    """Decides when a background title (re)generation is due.

    The driver ticks on every projected state update, so the cadence advances
    mid-turn as work happens, not only at turn boundaries. The initial title is
    due once ``initial_max_steps`` model steps accrue (a mix of assistant
    messages and tool activity — see ``count_model_steps``) or the first turn
    completes, whichever comes first, so a long single turn is titled while it
    runs. Afterwards the periodic refresh sharpens it every
    ``refresh_every_steps`` steps.

    Afterwards the cadence splits on the title model's cost, signalled by
    ``periodic``. On the cheap fast model (``periodic=True``) it refreshes every
    ``refresh_every_steps`` steps and after each compaction. On the possibly
    expensive active model (``periodic=False``) it drops the periodic refresh and
    caps total attempts at ``capped_max_generations``, so it only titles at the
    start and after a compaction, a handful of times at most. An attempt is
    counted when it begins, so a failing expensive model cannot retry without
    bound.

    A title we did not generate (a manual ``/rename``, or any title carried in on
    resume) blocks automatic refresh.
    """

    def __init__(self, policy: TitlePolicy = DEFAULT_TITLE_POLICY) -> None:
        self._policy = policy
        self._generated_title: str | None = None
        self._last_generated_step = -1
        self._last_compaction_id: str | None = None
        self._attempts = 0

    def is_manual(self, current_title: str | None) -> bool:
        return current_title is not None and current_title != self._generated_title

    def begin_if_due(
        self,
        *,
        step: int,
        current_title: str | None,
        compaction_id: str | None,
        turn_completing: bool,
        periodic: bool,
    ) -> bool:
        """Return whether a generation is due, counting the attempt when it is."""
        if self.is_manual(current_title):
            return False
        if not periodic and self._attempts >= self._policy.capped_max_generations:
            return False
        initial_pending = self._last_generated_step < 0
        new_compaction = (
            compaction_id is not None and compaction_id != self._last_compaction_id
        )
        initial_due = initial_pending and (
            step >= self._policy.initial_max_steps or (turn_completing and step >= 1)
        )
        periodic_due = (
            periodic
            and not initial_pending
            and step - self._last_generated_step >= self._policy.refresh_every_steps
        )
        if not (initial_due or new_compaction or periodic_due):
            return False
        # Consume the trigger up front: a generation that later fails or is
        # rejected never calls ``record``, so without this the same step or
        # compaction would look due on the very next event and re-fire the call.
        self._attempts += 1
        self._last_generated_step = step
        self._last_compaction_id = compaction_id
        return True

    def record(self, *, title: str) -> None:
        self._generated_title = title

    def restore(self, *, title: str, step: int, compaction_id: str | None) -> None:
        """Adopt a persisted auto title on resume so it keeps refreshing.

        Marks the title as our own (``is_manual`` stays False) and continues the
        step/compaction timeline from where the reloaded transcript stands, so
        the next refresh lands after the normal interval rather than immediately.
        """
        self._generated_title = title
        self._last_generated_step = step
        self._last_compaction_id = compaction_id


def _entry_text(entry: JsonObject) -> str:
    content = entry.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict):
            value = part.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


__all__ = [
    "DEFAULT_TITLE_POLICY",
    "TitleCadence",
    "TitlePolicy",
    "build_title_transcript",
    "clean_title",
    "count_model_steps",
    "execute_title_completion",
    "generate_session_title",
    "latest_compaction_id",
]
