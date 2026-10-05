from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
import dataclasses
import json
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
    report_request_sent,
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
- Name the overall task or feature the session is working on, not the most recent subtask or the last few turns.
- Titles are often cut off at the end in the UI, so order by importance: pinpoint identifier first, then the topic, then the action last. "PR 4821: billing retries, architecture review". Without an identifier, lead with the action and topic: "Fix Stripe webhook retries". The action is the first thing to drop when space runs short; never drop the identifier or the topic. Not "User asked to review a PR" and not "Fix Acme app PR 4821 review".
- Keep identifiers that pinpoint the subject (PR or issue numbers, ticket ids, package or file names), but drop repository names, URLs, and owner prefixes: "PR 4821", not "acme/app" or the link itself.
- Base the title on what the conversation reveals the task to be, including facts the assistant uncovered (for example, the real subject of a linked issue or ticket). If the user's opening message is just a link or a terse reference, title what it turned out to be about, not the reference itself.
- Ignore the assistant's process narration and preambles ("I'll explore…", "Let me…", "First, I'll…", "I'll start by…"); these describe activity, not the task.
- The transcript interleaves tool calls and tool results ("tool(name): …", "tool result(name): …") with the conversation. Use the facts they uncovered — a fetched PR's subject, the file being changed — to name the topic, not the tool activity itself.
- Prefer specific nouns from the code or domain over generic phrases. "Fix Stripe webhook retries" beats "Fix a bug".
- Plain text only, in sentence case. No quotes, backticks, markdown, code fences, or emoji.
- Always answer in English. If the transcript is in another language, translate the intent rather than transliterating.
- Prefer the shortest title that still captures the topic.
- If a `Current title:` is given, it names the session's overall task: keep it unless the transcript shows the session is genuinely about something else. If it is missing the topic and the transcript reveals it (for example the subject of a linked PR or issue), sharpen it. Never rewrite it to describe recent activity or the latest subtask.
- If the transcript is empty or describes no task, answer `New session`.

Respond with ONLY the title, on one line, with no quotes or explanation.
"""

_ELISION = "\n\n[…]\n\n"
_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WRAPPING_QUOTES = "\"'`“”‘’"


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
        entry_type = entry.get("type")
        if entry_type == "message":
            block = _message_block(entry, policy=policy)
        elif entry_type == "effect":
            block = _tool_block(entry, policy=policy)
        else:
            continue
        if block:
            blocks.append(block)
    transcript = "\n\n".join(blocks).strip()
    if len(transcript) <= policy.max_transcript_chars:
        return transcript
    head = transcript[: policy.head_transcript_chars].rstrip()
    tail = transcript[-policy.tail_transcript_chars :].lstrip()
    return f"{head}{_ELISION}{tail}"


def _message_block(entry: JsonObject, *, policy: TitlePolicy) -> str | None:
    role = entry.get("role")
    if role not in {"user", "assistant"}:
        return None
    text = _entry_text(entry).strip()
    if not text:
        return None
    return f"{role}: {text[: policy.max_message_chars]}"


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
    overlaid = dataclasses.replace(
        config, max_tokens=policy.max_tokens, correlation_id_sink=None
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
    report_request_sent(messages, config, route, purpose="title", iteration=0)
    metadata = (
        config.completion_metadata("title", 0)
        if config.completion_metadata is not None
        else None
    )
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
                metadata=metadata,
            )
        elif title_config.backend == "generic":
            result = await execute_generic_completion(
                messages,
                tools=[],
                config=title_config,
                credential=credential,
                route=route,
                metadata=metadata,
            )
        else:
            raise ValueError(f"Unsupported completion backend: {title_config.backend}")
    except Exception as exc:
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
    steps = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type == "message":
            if entry.get("role") == "assistant":
                steps += 1
            continue
        if entry_type != "effect":
            continue
        if entry.get("generationStatus") != "completed":
            continue
        state = entry.get("state")
        if not isinstance(state, dict):
            continue
        output = state.get("outputText")
        if isinstance(output, str) and output.strip():
            steps += 1
    return steps


def latest_compaction_id(entries: Sequence[JsonValue]) -> str | None:
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
    ) -> bool:
        if self.is_manual(current_title):
            return False
        if self._attempts >= self._policy.capped_max_generations:
            return False
        initial_pending = self._last_generated_step < 0
        new_compaction = (
            compaction_id is not None and compaction_id != self._last_compaction_id
        )
        initial_due = initial_pending and (
            step >= self._policy.initial_max_steps or turn_completing or new_compaction
        )
        confirm_due = (
            not initial_pending
            and step - self._last_generated_step >= self._policy.confirm_after_steps
        )
        if not (initial_due or confirm_due):
            return False
        self._attempts += 1
        self._last_generated_step = step
        self._last_compaction_id = compaction_id
        return True

    def record(self, *, title: str) -> None:
        self._generated_title = title


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


def _tool_block(entry: JsonObject, *, policy: TitlePolicy) -> str | None:
    detail = entry.get("detail")
    if not isinstance(detail, dict):
        detail = {}
    name = detail.get("toolName")
    if not isinstance(name, str) or not name:
        title = entry.get("title")
        name = title if isinstance(title, str) and title else "tool"
    lines: list[str] = []
    call = detail.get("input")
    if call is not None:
        call_text = (
            call
            if isinstance(call, str)
            else json.dumps(call, ensure_ascii=False, default=str)
        ).strip()
        if call_text and call_text not in {"{}", "[]", '""'}:
            lines.append(f"tool({name}): {call_text[: policy.max_tool_chars]}")
    state = entry.get("state")
    if isinstance(state, dict):
        output = state.get("outputText")
        if isinstance(output, str) and output.strip():
            lines.append(
                f"tool result({name}): {output.strip()[: policy.max_tool_chars]}"
            )
        elif state.get("status") == "failed":
            error = state.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            if isinstance(message, str) and message.strip():
                lines.append(
                    f"tool result({name}) failed: "
                    f"{message.strip()[: policy.max_tool_chars]}"
                )
    return "\n".join(lines) if lines else None


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
