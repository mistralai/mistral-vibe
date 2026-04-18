from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe.core.config import SessionLoggingConfig
from vibe.core.memory.manager import MemoryManager
from vibe.core.session.session_loader import SessionLoader
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import LLMMessage, Role, ToolStreamEvent

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class LearnArgs(BaseModel):
    max_sessions: int = Field(
        default=10, description="Maximum number of recent sessions to analyze."
    )
    scope: str = Field(
        default="project",
        description="Where to save learned memories: 'project' or 'global'.",
    )


class LearnedMemory(BaseModel):
    name: str
    type: str
    description: str
    content: str


class LearnResult(BaseModel):
    memories_created: int
    memories: list[LearnedMemory]
    sessions_analyzed: int


class LearnConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK


class Learn(
    BaseTool[LearnArgs, LearnResult, LearnConfig, BaseToolState],
    ToolUIData[LearnArgs, LearnResult],
):
    description: ClassVar[str] = (
        "Analyze past conversation sessions and automatically extract learnings. "
        "Detects user corrections and preferences, saving them as persistent memories."
    )

    async def run(
        self, args: LearnArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LearnResult, None]:
        if args.scope not in {"project", "global"}:
            raise ToolError("Scope must be 'project' or 'global'.")

        config = SessionLoggingConfig()
        sessions = SessionLoader.list_sessions(config)

        # Sort by end_time descending, limit to max_sessions
        sessions.sort(key=lambda s: s.get("end_time") or "", reverse=True)
        sessions = sessions[: args.max_sessions]

        manager = MemoryManager()
        existing_names = {m.name for m in manager.list_memories(scope=args.scope)}

        all_learned: list[LearnedMemory] = []
        sessions_analyzed = 0

        for session_info in sessions:
            session_path = SessionLoader.find_session_by_id(
                session_info["session_id"], config
            )
            if session_path is None:
                continue

            try:
                messages, _metadata = SessionLoader.load_session(session_path)
            except (ValueError, OSError):
                continue

            sessions_analyzed += 1
            learned = self._analyze_session(messages)

            for memory in learned:
                if memory.name in existing_names:
                    continue

                try:
                    manager.write_memory(
                        name=memory.name,
                        type=memory.type,
                        description=memory.description,
                        content=memory.content,
                        scope=args.scope,
                        source="auto-learned",
                    )
                    existing_names.add(memory.name)
                    all_learned.append(memory)
                except (ValueError, OSError):
                    continue

        yield LearnResult(
            memories_created=len(all_learned),
            memories=all_learned,
            sessions_analyzed=sessions_analyzed,
        )

    def _analyze_session(self, messages: list[LLMMessage]) -> list[LearnedMemory]:
        """Analyze a session's messages for learnable patterns."""
        learned: list[LearnedMemory] = []
        seen_slugs: set[str] = set()

        for i, msg in enumerate(messages):
            if msg.role != Role.user or not msg.content:
                continue

            content_lower = msg.content.strip().lower()

            # Skip very short messages (unlikely to be meaningful feedback)
            if len(content_lower) < 15:
                continue

            # Skip messages that look like tool invocations or meta-prompts
            if self._is_meta_message(content_lower):
                continue

            # Corrections take priority over preferences (elif, not if)
            if self._is_correction(content_lower):
                memory = self._extract_correction(msg.content, messages, i)
            elif self._is_preference(content_lower):
                memory = self._extract_preference(msg.content)
            else:
                continue

            if not memory:
                continue

            # Deduplicate within session by slug
            slug = self._slugify(memory.description[:50])
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            learned.append(memory)

        return learned

    def _is_meta_message(self, content: str) -> bool:
        """Filter out tool invocations and meta-prompts that aren't real feedback."""
        meta_patterns = [
            "use the ",
            "run the ",
            "call the ",
            "execute ",
            "analyze ",
            "search for ",
            "find ",
            "list ",
            "show me ",
            "read ",
            "open ",
            "create a ",
            "write a ",
            "help me ",
            "can you ",
            "could you ",
            "please ",
        ]
        return any(content.startswith(p) for p in meta_patterns)

    def _is_correction(self, content: str) -> bool:
        correction_starts = [
            "no,",
            "no ",
            "don't",
            "do not",
            "stop ",
            "wrong",
            "not like that",
            "that's wrong",
            "that's not",
            "nu,",
            "nu ",
            "nu face",
            "nu folosi",
            "opreste",
            "gresit",
        ]
        return any(content.startswith(s) for s in correction_starts)

    def _is_preference(self, content: str) -> bool:
        # Must start with a preference indicator, not just contain it
        # This prevents "use the learn tool" from matching
        preference_starts = [
            "always ",
            "never ",
            "prefer ",
            "from now on",
            "de acum",
            "mereu ",
            "niciodata ",
            "intotdeauna ",
        ]
        return any(content.startswith(s) for s in preference_starts)

    def _extract_correction(
        self, content: str, messages: list[LLMMessage], idx: int
    ) -> LearnedMemory | None:
        """Extract a correction into a memory."""
        prev_assistant = ""
        for j in range(idx - 1, -1, -1):
            msg_content = messages[j].content
            if messages[j].role == Role.assistant and msg_content:
                prev_assistant = msg_content[:200]
                break

        name = self._slugify(content[:50])
        body = (
            f"User correction: {content}\n\nContext: Assistant had said: {prev_assistant}"
            if prev_assistant
            else f"User correction: {content}"
        )
        return LearnedMemory(
            name=f"correction_{name}",
            type="feedback",
            description=content[:100],
            content=body,
        )

    def _extract_preference(self, content: str) -> LearnedMemory | None:
        name = self._slugify(content[:50])
        return LearnedMemory(
            name=f"preference_{name}",
            type="feedback",
            description=content[:100],
            content=f"User preference: {content}",
        )

    def _slugify(self, text: str) -> str:
        slug = text.lower().replace(" ", "_")
        slug = "".join(c for c in slug if c.isalnum() or c == "_")
        return slug[:60] or "memory"

    @classmethod
    def format_call_display(cls, args: LearnArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Analyzing recent sessions for learnings")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, LearnResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        result = event.result
        if result.memories_created == 0:
            return ToolResultDisplay(success=True, message="No new learnings found")
        return ToolResultDisplay(
            success=True,
            message=(
                f"Learned {result.memories_created} memories "
                f"from {result.sessions_analyzed} sessions"
            ),
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Learning from sessions"
