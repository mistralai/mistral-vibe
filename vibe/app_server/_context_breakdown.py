"""The `/context` breakdown: what fills the model's context window.

The total comes from the API: ``context_usage`` is the Harness's measurement of
the newest model call, so ``used_tokens`` is exact. The repartition is an
estimate: the Runtime reports the exact model input of each agent call (the
assembled system prompt, the messages and the tool catalogue), each category's
share of that payload's characters is applied to the measured input tokens, and
the completion's output tokens go to the conversation they joined. Estimates sum
to ``used_tokens`` exactly (largest-remainder rounding). Character ratios are a
good proxy because the global characters-per-token calibration cancels out; only
the relative density between categories matters.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import threading

from mistralai_vibe_local_harness.protocol import (
    RustAssistantMessage,
    RustEmbeddedResourceContentBlock,
    RustMessage,
    RustModelToolCallPart,
    RustReasoningPart,
    RustSystemMessage,
    RustTextContentBlock,
    RustTextResourceContents,
    RustToolDefinition,
    RustToolMessage,
)
from mistralai_vibe_local_harness.vibe import ModelRequest
from vibe.app_server._skill_invocation import is_skill_content
from vibe.app_server.models import PublicSessionState
from vibe.app_server.protocol import (
    ContextBreakdown,
    ContextCategory,
    ContextCategoryId,
)
from vibe.observability.logging import logger

# Beside each session's store: the category sizes of its newest model input.
_SAVED_SIZES_FILENAME = "context-breakdown.json"
# A child's turn IDs are ``<child session ID>:turn:<generation>``.
_CHILD_TURN_SEPARATOR = ":turn:"
# The Core's model-facing name for the Runtime's skill loader.
_SKILL_TOOL = "skill"
_CATEGORY_ORDER = (
    ContextCategoryId.SYSTEM,
    ContextCategoryId.TOOLS,
    ContextCategoryId.SKILLS,
    ContextCategoryId.CONVERSATION,
)


@dataclass(frozen=True, slots=True)
class PayloadSizes:
    """Characters of one model input, by category."""

    system: int = 0
    tools: int = 0
    skills: int = 0
    conversation: int = 0

    def weights(self) -> list[int]:
        """The sizes in ``_CATEGORY_ORDER``."""
        return [self.system, self.tools, self.skills, self.conversation]


class ModelRequestLog:
    """The sizes of the newest agent model input of each session.

    The root and its subagents share one sink, which the Runtime may call off
    the event loop. A child's calls are keyed by the child session ID, the
    root's by the session the adapter bound. Only sizes are kept, never the
    payload. Each recording is also written next to the session's store, so a
    reopened session still has the split of the call its saved usage measured.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, PayloadSizes] = {}
        self._root_session_id: str | None = None
        self._sessions_dir: Path | None = None

    def bind(self, root_session_id: str, sessions_dir: Path | None) -> None:
        """Key the root's calls by ``root_session_id``; persist under
        ``sessions_dir/<session ID>/`` when it is set.
        """
        with self._lock:
            self._root_session_id = root_session_id
            self._sessions_dir = sessions_dir

    def record(self, request: ModelRequest) -> None:
        sizes = payload_sizes(request.messages, request.tools)
        child, separator, _ = request.turn_id.rpartition(_CHILD_TURN_SEPARATOR)
        with self._lock:
            session_id = child if separator else self._root_session_id
            if session_id is None:
                return
            self._latest[session_id] = sizes
            sessions_dir = self._sessions_dir
        if sessions_dir is not None:
            _write_sizes(sessions_dir / session_id, sizes)

    def latest(self, session_id: str) -> PayloadSizes | None:
        """The newest sizes this process recorded for ``session_id``."""
        with self._lock:
            return self._latest.get(session_id)

    def saved(self, session_id: str) -> PayloadSizes | None:
        """The sizes a previous process saved for ``session_id``, if any."""
        with self._lock:
            sessions_dir = self._sessions_dir
        if sessions_dir is None:
            return None
        return _read_sizes(sessions_dir / session_id)


def _write_sizes(session_dir: Path, sizes: PayloadSizes) -> None:
    """Replace the saved sizes; a session whose store is not on disk (yet)
    keeps them in memory only.
    """
    if not session_dir.is_dir():
        return
    target = session_dir / _SAVED_SIZES_FILENAME
    temporary = target.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(asdict(sizes)), encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        logger.debug("Could not save the context breakdown", exc_info=True)


def _read_sizes(session_dir: Path) -> PayloadSizes | None:
    try:
        raw = json.loads((session_dir / _SAVED_SIZES_FILENAME).read_text("utf-8"))
        return PayloadSizes(**{
            name: int(raw[name]) for name in PayloadSizes.__dataclass_fields__
        })
    except (OSError, ValueError, TypeError, KeyError):
        return None


def payload_sizes(
    messages: Iterable[RustMessage], tools: Iterable[RustToolDefinition]
) -> PayloadSizes:
    """Split one model input into categories by character count.

    System messages are the system prompt; tool definitions are the tool
    catalogue as serialized for the provider; skill bodies are the results of
    the ``skill`` tool and the bodies a client inlined into a user message;
    everything else is the conversation, tool calls and results included.
    """
    system = skills = conversation = 0
    for message in messages:
        if isinstance(message, RustSystemMessage):
            system += _text_characters(message.content)
        elif isinstance(message, RustToolMessage) and message.name == _SKILL_TOOL:
            skills += _text_characters(message.content)
        elif isinstance(message, RustAssistantMessage):
            conversation += _assistant_characters(message)
        else:
            inlined = sum(
                len(block.text)
                for block in message.content
                if isinstance(block, RustTextContentBlock)
                and is_skill_content(block.text)
            )
            skills += inlined
            conversation += _text_characters(message.content) - inlined
    tool_characters = sum(
        len(tool.name)
        + len(tool.description)
        + len(json.dumps(tool.parameters, separators=(",", ":")))
        for tool in tools
    )
    return PayloadSizes(
        system=system, tools=tool_characters, skills=skills, conversation=conversation
    )


def _assistant_characters(message: RustAssistantMessage) -> int:
    total = 0
    for part in message.content:
        if isinstance(part, RustModelToolCallPart):
            total += len(part.name) + len(part.arguments.raw)
        elif isinstance(part, RustReasoningPart):
            total += sum(len(getattr(item, "text", "")) for item in part.content)
        else:
            total += _text_characters([part])
    return total


def _text_characters(content: Sequence[object]) -> int:
    """Text-bearing characters of content blocks; media are not text."""
    total = 0
    for block in content:
        if isinstance(block, RustTextContentBlock):
            total += len(block.text)
        elif isinstance(block, RustEmbeddedResourceContentBlock) and isinstance(
            block.resource, RustTextResourceContents
        ):
            total += len(block.resource.text)
    return total


def _largest_remainder(total: int, weights: list[int]) -> list[int]:
    """Split `total` proportionally to `weights`, summing to `total` exactly."""
    weight_sum = sum(weights)
    if total <= 0 or weight_sum <= 0:
        return [0] * len(weights)
    floors = [total * weight // weight_sum for weight in weights]
    remainder = total - sum(floors)
    order = sorted(
        range(len(weights)),
        key=lambda i: (total * weights[i]) % weight_sum,
        reverse=True,
    )
    for i in order[:remainder]:
        floors[i] += 1
    return floors


def build_context_breakdown(
    state: PublicSessionState,
    *,
    payload: PayloadSizes | None,
    context_window: int,
    compaction_threshold: int,
) -> ContextBreakdown:
    """Assemble the breakdown from a session snapshot and its newest model input.

    ``measured`` is false until the first model call: with no API reading there
    is no total to repartition, so the caller shows no estimates. A measured
    session whose model input this process has not seen (one reopened before its
    next call) reports the total without a repartition. Neither does a reading
    compaction zeroed: the newest input describes the context it replaced, so it
    stays unsplit until the next call measures the summary.
    """
    usage = state.session.context_usage
    if usage is not None and usage.input_tokens + usage.output_tokens == 0:
        payload = None
    weights = (payload or PayloadSizes()).weights()
    tokens = [0] * len(weights)
    if usage is not None and payload is not None:
        tokens = _largest_remainder(usage.input_tokens, weights)
        tokens[_CATEGORY_ORDER.index(ContextCategoryId.CONVERSATION)] += (
            usage.output_tokens
        )
    categories = [
        ContextCategory(id=category, characters=characters, estimated_tokens=estimated)
        for category, characters, estimated in zip(
            _CATEGORY_ORDER, weights, tokens, strict=True
        )
        if characters > 0 or estimated > 0
    ]
    return ContextBreakdown(
        context_window=context_window,
        compaction_threshold=min(compaction_threshold, context_window),
        used_tokens=usage.input_tokens + usage.output_tokens if usage else 0,
        measured=usage is not None,
        total_characters=sum(weights),
        categories=categories,
    )
