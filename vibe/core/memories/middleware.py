from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.memories.manager import MemoryManager
from vibe.core.middleware import ConversationMiddleware, MiddlewareResult

if TYPE_CHECKING:
    from vibe.core.middleware import ConversationContext


class MemoryLoadingMiddleware(ConversationMiddleware):
    """Middleware that loads memories into context based on triggers."""

    def __init__(self, memory_manager_getter: callable) -> None:
        """Initialize the middleware.

        Args:
            memory_manager_getter: Callable that returns a MemoryManager instance
        """
        self._memory_manager_getter = memory_manager_getter
        self._memories_loaded = False
        self._last_context_hash = None

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        """Load memories before each turn if needed."""
        memory_manager = self._memory_manager_getter()

        # Only load "always" memories at strategic points, not every turn
        current_context_hash = self._get_context_hash(context.messages)

        # Load memories if:
        # 1. We haven't loaded them yet (first turn)
        # 2. Context has changed significantly (after compaction, etc.)
        should_load = not self._memories_loaded or (current_context_hash != self._last_context_hash)

        if should_load:
            always_memories = memory_manager.get_memories_for_trigger("always")

            if always_memories:
                memory_messages = memory_manager.convert_to_llm_messages(always_memories)

                # Insert memories at the beginning of context (after system message)
                if len(context.messages) > 0:
                    # Find the position after system message
                    insert_pos = 1
                    for i, msg in enumerate(context.messages):
                        if msg.role == "system":
                            insert_pos = i + 1
                            break

                    # Insert memory messages
                    for msg in reversed(memory_messages):
                        context.messages.insert(insert_pos, msg)

                # Update state to avoid reloading unnecessarily
                self._memories_loaded = True
                self._last_context_hash = current_context_hash

        return MiddlewareResult()

    def _get_context_hash(self, messages: list) -> str:
        """Get a simple hash representing the current context state."""
        # For simplicity, use the number of messages as a proxy for context changes
        # In a more sophisticated implementation, we could hash message content
        return f"messages_{len(messages)}"

    def reset(self, reset_reason: str = "stop") -> None:
        """Reset the middleware state."""
        self._memories_loaded = False
        self._last_context_hash = None