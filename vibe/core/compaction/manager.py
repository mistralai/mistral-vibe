from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Literal, Protocol

from vibe.core.compaction.context import (
    collect_prior_user_messages,
    drop_oldest_round,
    extract_summary,
    render_compaction_context,
)
from vibe.core.prompts import UtilityPrompt
from vibe.core.types import ContextTooLongError, LLMMessage, Role

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig, VibeConfigSchema
    from vibe.core.telemetry.send import TelemetryClient
    from vibe.core.telemetry.types import TelemetryCallType
    from vibe.core.types import (
        AgentStats,
        AvailableTool,
        LLMChunk,
        MessageList,
        StrToolChoice,
    )

_COMPACTION_PTL_RETRIES = 3
CompactionFailureReason = Literal["tool_call", "empty_summary"]


class CompactionFailedError(Exception):
    """Raised when a compaction turn did not produce a usable summary."""

    def __init__(self, reason: str) -> None:
        self.reason = reason  # a CompactionFailureReason
        super().__init__(f"Compaction did not produce a summary (reason={reason}).")


class CompletionFn(Protocol):
    """One accounted, non-streaming model call (e.g. ``AgentLoop._complete``)."""

    async def __call__(
        self,
        *,
        model: ModelConfig,
        messages: Sequence[LLMMessage],
        tools: list[AvailableTool] | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        call_type: TelemetryCallType | None,
    ) -> LLMChunk: ...


class CompactionManager:
    """Summarizes a conversation into a compact-context envelope.

    Every model call goes through the injected ``complete`` callable (so usage
    is always accounted), and the summary is generated on a local copy of the
    messages — the live ``MessageList`` is only mutated by the final
    ``reset([system, envelope])`` on success, so a failure never leaves a
    partially trimmed conversation behind.
    """

    def __init__(
        self,
        *,
        messages: MessageList,
        stats_getter: Callable[[], AgentStats],
        config_getter: Callable[[], VibeConfigSchema],
        complete: CompletionFn,
        available_tools: Callable[[], list[AvailableTool]],
        tool_choice: Callable[[], StrToolChoice | AvailableTool],
        save: Callable[[], Awaitable[None]],
        reset_session: Callable[[], Awaitable[None]],
        telemetry_client: TelemetryClient,
        session_ids: Callable[[], tuple[str, str | None]],
    ) -> None:
        self._messages = messages
        self._stats = stats_getter
        self._config = config_getter
        self._complete = complete
        self._available_tools = available_tools
        self._tool_choice = tool_choice
        self._save = save
        self._reset_session = reset_session
        self._telemetry = telemetry_client
        self._session_ids = session_ids

    async def compact(self, extra_instructions: str = "") -> str:
        summary_prefix = UtilityPrompt.COMPACT_SUMMARY_PREFIX.read()
        snapshot = list(self._messages)
        prior_user_messages = collect_prior_user_messages(snapshot, summary_prefix)

        summary = await self._summarize(snapshot, extra_instructions)
        if summary is None:
            # _summarize already reported the failure; substitute a placeholder.
            summary = "(no summary available)"

        system_message = snapshot[0]
        envelope = LLMMessage(
            role=Role.user,
            content=render_compaction_context(prior_user_messages, summary),
            injected=True,
        )
        self._messages.reset([system_message, envelope])
        await self._reset_session()
        # Context size is unknown without an API call; the next LLM turn
        # recomputes it accurately from real usage.
        self._stats().context_tokens = 0
        await self._save()
        return summary

    async def _summarize(
        self, snapshot: list[LLMMessage], extra_instructions: str
    ) -> str | None:
        request = self._config().compaction_prompt
        if extra_instructions:
            request += f"\n\n## Additional Instructions\n{extra_instructions}"

        summary, working, reason = await self._primary(snapshot, request)
        if summary is not None:
            return summary

        # Strict mode has no fallback and is terminal; otherwise try the dedicated
        # fallback before giving up. Either way, report the primary's failure mode
        # (the fallback can only ever fail as an empty summary, so the primary
        # reason — e.g. tool_call — is the informative one).
        assert reason is not None  # a None summary always carries a reason
        if not self._config().raise_on_compaction_failure:
            recovered = await self._fallback(working, request)
            if recovered is not None:
                return recovered
        self._send_compaction_failed(reason)
        if self._config().raise_on_compaction_failure:
            raise CompactionFailedError(reason)
        return None

    async def _primary(
        self, snapshot: list[LLMMessage], request: str
    ) -> tuple[str | None, list[LLMMessage], CompactionFailureReason | None]:
        # Cache-friendly: the request rides on the live conversation's token
        # prefix. Works on a local copy, so the live message list is untouched.
        request_message = LLMMessage(role=Role.user, content=request)
        result, working = await self._summarize_call(
            list(snapshot),
            lambda history: [*history, request_message],
            model=self._config().get_compaction_model(),
            tools=self._available_tools(),
            tool_choice=self._tool_choice(),
        )
        if result.message.tool_calls:
            return None, working, "tool_call"
        summary = extract_summary(result.message.content or "")
        if summary is None:
            return None, working, "empty_summary"
        return summary, working, None

    async def _fallback(self, history: list[LLMMessage], request: str) -> str | None:
        # Dedicated summarizer call: fresh system prompt, no thinking, no tools.
        # Breaks the prompt cache on purpose — a reliable summary is worth it.
        model = (
            self._config().get_compaction_model().model_copy(update={"thinking": "off"})
        )
        result, _ = await self._summarize_call(
            list(history),
            lambda working: [
                LLMMessage(
                    role=Role.system, content=UtilityPrompt.COMPACT_SYSTEM.read()
                ),
                LLMMessage(
                    role=Role.user,
                    content=f"{request}\n\n## Conversation Transcript\n"
                    f"{self._render_transcript(working)}",
                ),
            ],
            model=model,
            tools=None,
            tool_choice=None,
        )
        return extract_summary(result.message.content or "")

    async def _summarize_call(
        self,
        working: list[LLMMessage],
        build_messages: Callable[[list[LLMMessage]], list[LLMMessage]],
        *,
        model: ModelConfig,
        tools: list[AvailableTool] | None,
        tool_choice: StrToolChoice | AvailableTool | None,
    ) -> tuple[LLMChunk, list[LLMMessage]]:
        # Summarize `working`; on overflow drop the oldest round and retry.
        # Returns the model result and the (possibly trimmed) history it used.
        tries_left = _COMPACTION_PTL_RETRIES
        while True:
            try:
                result = await self._complete(
                    model=model,
                    messages=build_messages(working),
                    tools=tools,
                    tool_choice=tool_choice,
                    call_type="secondary_call",
                )
                return result, working
            except ContextTooLongError:
                trimmed = drop_oldest_round(working)
                if tries_left == 0 or trimmed is None:
                    raise
                working = trimmed
                tries_left -= 1

    def _render_transcript(self, messages: Sequence[LLMMessage]) -> str:
        # Render each turn to text, keeping tool calls (the agent's actions) so
        # the summarizer sees what invoked the tool results it also sees.
        # Reasoning is intentionally dropped — it's ephemeral and would bloat the
        # transcript compaction is trying to shrink.
        blocks: list[str] = []
        for message in messages:
            if message.role == Role.system:
                continue
            parts: list[str] = []
            text = (message.content or "").strip()
            if text:
                parts.append(text)
            for call in message.tool_calls or []:
                if call.function is not None:
                    parts.append(
                        f"[calls {call.function.name}({call.function.arguments})]"
                    )
            if not parts:
                continue
            blocks.append(f"### {message.role.value}\n" + "\n".join(parts))
        return "\n\n".join(blocks)

    def _send_compaction_failed(self, reason: CompactionFailureReason) -> None:
        session_id, parent_session_id = self._session_ids()
        self._telemetry.send_compaction_failed(
            reason=reason, session_id=session_id, parent_session_id=parent_session_id
        )
