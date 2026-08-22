"""``AgentLoop`` subclass that mirrors vibe's lifecycle onto pi hooks.

Everything Accordion needs lives here as an override; ``vibe/core/agent_loop``
is untouched. With no Accordion checkout configured ``build_agent_loop``
returns a plain ``AgentLoop`` and this module's behaviour is byte-for-byte
upstream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Sequence
import contextlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from accordion_vibe._bridge import AccordionBridge
from accordion_vibe._config import resolve_accordion_app, resolve_accordion_repo
from vibe.core.agent_loop import AgentLoop
from vibe.core.hooks.models import HookEvent
from vibe.core.middleware import AutoCompactMiddleware
from vibe.core.types import LLMMessage, Role, ToolResultEvent, ToolStreamEvent
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from opentelemetry import trace

    from vibe.core.agent_loop import ToolDecision
    from vibe.core.config import ModelConfig
    from vibe.core.llm.format import ResolvedToolCall
    from vibe.core.middleware import ConversationContext
    from vibe.core.tools.base import BaseTool
    from vibe.core.types import BaseEvent, LLMChunk, LLMUsage

__all__ = ["AccordionAgentLoop", "build_agent_loop"]

_ACCORDION_TOOL_NAMES = ("unfold", "recall")


def _skill_search_root(raw: str) -> Path | None:
    """Turn one pi skill path into the directory vibe should search."""
    path = Path(raw)
    root = path.parent if (path / "SKILL.md").is_file() else path
    return root.resolve() if root.is_dir() else None


def _harness_version() -> str:
    try:
        from importlib.metadata import version

        return version("mistral-vibe")
    except Exception:  # not installed as a distribution (editable checkout)
        return "unknown"


def build_agent_loop(
    factory: Callable[..., AgentLoop] = AgentLoop, /, **kwargs: Any
) -> AgentLoop:
    """Construct the loop vibe should run.

    This is the single in-tree seam: ``_AgentLoopBlueprint.build`` calls it
    instead of ``AgentLoop(...)``. Without an Accordion checkout it defers to
    ``factory``, so the bridge cannot affect a plain install.

    ``factory`` is the caller's own ``AgentLoop`` name rather than an implicit
    default: several app-server tests monkeypatch that name to capture the
    construction arguments, and stepping aside for any substituted factory
    keeps that seam working — a test double is never something to subclass.
    """
    orchestrator = kwargs.get("config_orchestrator")
    config = getattr(orchestrator, "config", None)
    if factory is not AgentLoop or resolve_accordion_repo(config) is None:
        return factory(**kwargs)
    return AccordionAgentLoop(**kwargs)


class AccordionAgentLoop(AgentLoop):
    """An ``AgentLoop`` that forwards its lifecycle to an Accordion sidecar."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._accordion: AccordionBridge | None = None
        super().__init__(*args, **kwargs)
        self._accordion_system_prompt: str | None = None
        self._accordion_watermark = 0
        self._accordion_started_ids: set[str] = set()
        self._accordion_turn_index = 0
        self._accordion_folding_generation = 0
        self._accordion_model: str | None = None
        self._accordion_main_call = False
        self._accordion_tools_checked = False
        self._accordion_skills_applied = False
        self._attach_accordion()

    # -- attach / detach ---------------------------------------------------

    def _attach_accordion(self) -> None:
        repo = resolve_accordion_repo(self.config)
        if repo is None or self._is_subagent:
            return
        app = resolve_accordion_app(self.config)
        try:
            self._accordion = AccordionBridge(
                repo,
                session_id=self.session_id,
                cwd=self.cwd,
                harness_version=_harness_version(),
                session_file=self._accordion_session_file(),
                model=self._accordion_active_model(),
                flags={"accordion-app": app} if app else None,
            )
            self._register_accordion_tools()
            self._accordion.start_in_background()
        except Exception as exc:  # attaching must never break session startup
            logger.warning("accordion: bridge attach failed: %s", exc)
            self._accordion = None

    def _accordion_active_model(self) -> dict[str, Any] | None:
        try:
            return self._accordion_model_payload(self.config.get_active_model())
        except ValueError:
            return None

    def _accordion_session_file(self) -> str | None:
        directory = getattr(self.session_logger, "session_dir", None)
        return str(directory / "messages.jsonl") if directory is not None else None

    def _register_accordion_tools(self) -> None:
        """Register ``unfold``/``recall`` directly on this loop's ToolManager.

        Registering the classes beats appending a search path: no dynamic
        import by file path (which a frozen build would not resolve) and the
        tools exist only for a bridged session.
        """
        from accordion_vibe.tools.accordion_tools import Recall, Unfold

        manager = self.tool_manager
        for tool_class in (Unfold, Recall):
            manager._register_discovered_tool_variant(tool_class, is_custom=True)
            manager._tool_descriptions.setdefault(
                tool_class.get_name(), tool_class.description
            )

    def _check_advertised_tools(self, bridge: AccordionBridge) -> None:
        """Warn once when the sidecar's tools are not the pair we registered.

        Generating tools from ``ready.tools`` JSON schemas is out of scope for
        v1, so a mismatch means the agent is missing a tool the extension
        expects it to have.
        """
        if self._accordion_tools_checked:
            return
        self._accordion_tools_checked = True
        advertised = {str(spec.get("name")) for spec in bridge.tool_specs()}
        missing = advertised.difference(_ACCORDION_TOOL_NAMES)
        if missing:
            logger.warning(
                "accordion: sidecar advertises unregistered tools %s; "
                "only %s are bridged",
                sorted(missing),
                list(_ACCORDION_TOOL_NAMES),
            )

    def _apply_accordion_skill_paths(self, bridge: AccordionBridge) -> None:
        """Fold ``resources_discover``'s skill paths into the SkillManager.

        pi hands back the skill directories themselves; vibe searches their
        *parent* (``<base>/<name>/SKILL.md``), so each one is lifted a level.
        Done here rather than on the handshake thread so the manager is only
        ever mutated from the loop's own thread.
        """
        if self._accordion_skills_applied:
            return
        self._accordion_skills_applied = True
        try:
            manager = self.skill_manager
            seen = {p.resolve() for p in manager._search_paths}
            added: list[Path] = []
            # Sibling skills share one parent, so dedupe against what has
            # already been added, not just against what was there before.
            for root in (_skill_search_root(raw) for raw in bridge.skill_paths):
                if root is None or root in seen:
                    continue
                seen.add(root)
                added.append(root)
            if not added:
                return
            manager._search_paths.extend(added)
            manager.available_skills = MappingProxyType(
                manager._apply_filters(manager._discover_skills())
            )
        except Exception as exc:  # a missing skill must never break a turn
            logger.warning("accordion: could not add sidecar skill paths: %s", exc)

    def _accordion_ready(self) -> AccordionBridge | None:
        bridge = self._accordion
        return bridge if bridge is not None and bridge.attached else None

    async def aclose(self) -> None:
        bridge = self._accordion
        self._accordion = None
        try:
            await super().aclose()
        finally:
            if bridge is not None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(bridge.close)

    # -- the hot path ------------------------------------------------------

    def _messages_for_backend(
        self, messages: Sequence[LLMMessage], active_model: ModelConfig
    ) -> Sequence[LLMMessage]:
        base = super()._messages_for_backend(messages, active_model)
        base = self._apply_run_system_prompt(base)
        bridge = self._accordion_ready()
        # Only the main agent completion consults the sidecar. Title generation,
        # compaction summaries and teleport summaries all build a synthetic list,
        # so identity against ``self.messages`` is the discriminator. It is set
        # here rather than guessed later: this runs immediately before the
        # backend call whose usage ``_update_stats`` will report.
        self._accordion_main_call = messages is self.messages
        if bridge is None or messages is not self.messages:
            return base
        replacement = bridge.context(base, self._accordion_model_payload(active_model))
        return replacement if replacement is not None else base

    def _apply_run_system_prompt(
        self, messages: Sequence[LLMMessage]
    ) -> Sequence[LLMMessage]:
        override = self._accordion_system_prompt
        if override is None or not messages or messages[0].role != Role.system:
            return messages
        head = messages[0].model_copy(update={"content": override})
        return [head, *messages[1:]]

    def _accordion_model_payload(self, model: ModelConfig) -> dict[str, Any]:
        # vibe has no declared provider context window; the auto-compact
        # threshold is the number every other surface treats as one.
        return {
            "id": model.name,
            "provider": model.provider,
            "contextWindow": model.auto_compact_threshold,
        }

    # -- run / turn lifecycle ---------------------------------------------

    async def act(
        self, msg: str, *args: Any, **kwargs: Any
    ) -> AsyncGenerator[BaseEvent, None]:
        bridge = self._accordion
        if bridge is not None and not bridge.attached:
            await asyncio.to_thread(bridge.ensure_started)
        bridge = self._accordion_ready()
        if bridge is None:
            async with contextlib.aclosing(super().act(msg, *args, **kwargs)) as run:
                async for event in run:
                    yield event
            return

        # ``super().act`` is @requires_init; seeding the sidecar reads
        # self.messages, which deferred init still inserts the system prompt
        # into, so wait for the same barrier before looking at it.
        await self.wait_until_ready()
        self._check_advertised_tools(bridge)
        # session_start is what triggers resources_discover, so the skill paths
        # only exist on the far side of it.
        bridge.session_start("start", self.messages)
        self._apply_accordion_skill_paths(bridge)
        self._accordion_watermark = len(self.messages)
        self._accordion_system_prompt = await asyncio.to_thread(
            bridge.before_agent_start, msg
        )
        run_start = len(self.messages)
        bridge.emit("agent_start")
        try:
            async with contextlib.aclosing(super().act(msg, *args, **kwargs)) as run:
                async for event in run:
                    yield event
        finally:
            self._accordion_system_prompt = None
            self._drain_messages()
            bridge.emit(
                "agent_end", messages=self._accordion_dump(self.messages[run_start:])
            )

    async def _perform_llm_turn(self) -> AsyncGenerator[BaseEvent, None]:
        bridge = self._accordion_ready()
        if bridge is None:
            async with contextlib.aclosing(super()._perform_llm_turn()) as turn:
                async for event in turn:
                    yield event
            return

        self._drain_messages()
        bridge.emit("turn_start", turnIndex=self._accordion_turn_index)
        self._accordion_turn_index += 1
        before = len(self.messages)
        try:
            async with contextlib.aclosing(super()._perform_llm_turn()) as turn:
                async for event in turn:
                    yield event
        finally:
            self._emit_turn_end(bridge, before)
            self._drain_messages()

    def _emit_turn_end(self, bridge: AccordionBridge, before: int) -> None:
        produced = list(self.messages[before:])
        assistant = next((m for m in produced if m.role == Role.assistant), None)
        if assistant is None:
            return
        bridge.emit(
            "turn_end",
            message=assistant.model_dump(mode="json"),
            toolResults=self._accordion_dump([
                m for m in produced if m.role == Role.tool
            ]),
        )

    async def _chat_streaming(self) -> AsyncGenerator[LLMChunk]:
        bridge = self._accordion_ready()
        aggregate: LLMChunk | None = None
        started = False
        try:
            async with contextlib.aclosing(super()._chat_streaming()) as stream:
                async for chunk in stream:
                    if bridge is not None:
                        aggregate = chunk if aggregate is None else aggregate + chunk
                        started = self._emit_streaming_message(
                            bridge, aggregate, started
                        )
                    yield chunk
        finally:
            # The drain would otherwise emit a second message_start for the
            # message this stream already announced. The id is only reliable on
            # the aggregate: early chunks can arrive without one.
            if started and aggregate is not None:
                self._accordion_started_ids.add(aggregate.message.message_id or "")

    def _emit_streaming_message(
        self, bridge: AccordionBridge, aggregate: LLMChunk, started: bool
    ) -> bool:
        message = aggregate.message
        if not started:
            bridge.emit("message_start", message=message.model_dump(mode="json"))
            return True
        bridge.emit_message_update(message)
        return True

    def _drain_messages(self) -> None:
        """Emit ``message_start``/``message_end`` for newly appended messages.

        vibe has no per-message notification, so the bridge diffs the log at
        turn boundaries instead. A streamed assistant message already got its
        ``message_start`` from ``_chat_streaming``; everything else gets a
        degenerate start immediately before its end.
        """
        bridge = self._accordion_ready()
        if bridge is None:
            self._accordion_watermark = len(self.messages)
            return
        appended = list(self.messages[self._accordion_watermark :])
        self._accordion_watermark = len(self.messages)
        for message in appended:
            payload = message.model_dump(mode="json")
            key = message.message_id or ""
            if key not in self._accordion_started_ids:
                bridge.emit("message_start", message=payload)
            self._accordion_started_ids.discard(key)
            bridge.emit("message_end", message=payload)

    @staticmethod
    def _accordion_dump(messages: Sequence[LLMMessage]) -> list[dict[str, Any]]:
        return [m.model_dump(mode="json") for m in messages]

    # -- tools -------------------------------------------------------------

    async def _invoke_tool(
        self,
        tool_call: ResolvedToolCall,
        tool_instance: BaseTool,
        tool_input: dict[str, Any],
        decision: ToolDecision,
        *,
        span: trace.Span,
    ) -> AsyncGenerator[ToolResultEvent | ToolStreamEvent | HookEvent]:
        bridge = self._accordion_ready()
        inner = super()._invoke_tool(
            tool_call, tool_instance, tool_input, decision, span=span
        )
        if bridge is None:
            async with contextlib.aclosing(inner) as items:
                async for item in items:
                    yield item
            return

        bridge.emit(
            "tool_execution_start",
            toolCallId=tool_call.call_id,
            toolName=tool_call.tool_name,
            args=tool_input,
        )
        result = ""
        is_error = False
        try:
            async with contextlib.aclosing(inner) as items:
                async for item in items:
                    if isinstance(item, ToolResultEvent) and item.result is not None:
                        result = json.dumps(
                            item.result.model_dump(mode="json"), default=str
                        )
                    yield item
        except Exception as exc:
            is_error = True
            result = str(exc)
            raise
        finally:
            bridge.emit(
                "tool_execution_end",
                toolCallId=tool_call.call_id,
                toolName=tool_call.tool_name,
                result=result,
                isError=is_error,
            )

    # -- state changes -----------------------------------------------------

    def _get_context(self) -> ConversationContext:
        # Called once per turn immediately before the middleware pipeline runs,
        # which makes it the cheapest safe place to react to sidecar state.
        self._sync_accordion_state()
        return super()._get_context()

    def _sync_accordion_state(self) -> None:
        bridge = self._accordion_ready()
        if bridge is None:
            return
        if bridge.folding_generation != self._accordion_folding_generation:
            self._accordion_folding_generation = bridge.folding_generation
            self._setup_middleware()
        try:
            model = self.config.get_active_model()
        except ValueError:
            return
        if model.name == self._accordion_model:
            return
        previous = self._accordion_model
        self._accordion_model = model.name
        bridge.emit(
            "model_select",
            model=self._accordion_model_payload(model),
            previous={"id": previous} if previous else None,
            source="restore" if previous is None else "select",
        )

    def _setup_middleware(self) -> None:
        super()._setup_middleware()
        bridge = getattr(self, "_accordion", None)
        if bridge is None or not bridge.folding_enabled:
            return
        # Accordion owns the context while folding is on; vibe's own
        # auto-compaction would fight it. Manual /compact is untouched.
        self.middleware_pipeline.middlewares = [
            m
            for m in self.middleware_pipeline.middlewares
            if not isinstance(m, AutoCompactMiddleware)
        ]

    def _update_stats(self, usage: LLMUsage, time_seconds: float) -> None:
        super()._update_stats(usage, time_seconds)
        bridge = self._accordion_ready()
        # Calibration pairs a `usage` with the assistant message_end that
        # follows it, so a secondary call's usage would poison the anchor.
        if bridge is None or not self._accordion_main_call:
            return
        try:
            window = self.config.get_active_model().auto_compact_threshold
        except ValueError:
            window = 0
        bridge.emit(
            "usage",
            promptTokens=usage.prompt_tokens,
            completionTokens=usage.completion_tokens,
            contextWindow=window,
        )

    async def compact(self, extra_instructions: str = "") -> str:
        bridge = self._accordion_ready()
        if bridge is not None:
            bridge.emit("session_before_compact")
        summary = await super().compact(extra_instructions)
        bridge = self._accordion_ready()
        if bridge is not None:
            # The POST-compaction list: without it the sidecar can only
            # reconcile against the history compaction just removed.
            bridge.emit(
                "session_compact",
                summary=summary,
                messages=self._accordion_dump(self.messages[:]),
            )
            self._accordion_watermark = len(self.messages)
            self._accordion_started_ids.clear()
        return summary
