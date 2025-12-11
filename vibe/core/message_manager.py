"""Message Manager for ChefChat Agent.

Handles message history, system prompt initialization, and message observation.
Part of the Agent decomposition (Refactor B.1).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from vibe.core.system_prompt import get_universal_system_prompt
from vibe.core.types import LLMMessage, Role
from vibe.core.utils import CancellationReason, get_user_cancellation_message

if TYPE_CHECKING:
    from vibe.cli.mode_manager import ModeManager
    from vibe.core.config import VibeConfig
    from vibe.core.tools.manager import ToolManager


class MessageManager:
    """Manages chat history and message observation."""

    def __init__(
        self,
        config: VibeConfig,
        tool_manager: ToolManager,
        mode_manager: ModeManager | None = None,
        observer: Callable[[LLMMessage], None] | None = None,
    ) -> None:
        """Initialize MessageManager with system prompt."""
        self.config = config
        self.observer = observer
        self._last_observed_index: int = 0

        # Initialize messages with System Prompt
        system_prompt = get_universal_system_prompt(tool_manager, config, mode_manager)
        self.messages: list[LLMMessage] = [
            LLMMessage(role=Role.system, content=system_prompt)
        ]

        if self.observer:
            self.observer(self.messages[0])
            self._last_observed_index = 1

    def add_message(self, message: LLMMessage) -> None:
        """Add a message to history."""
        self.messages.append(message)

    def flush_new_messages(self) -> None:
        """Notify observer of new messages."""
        if not self.observer:
            return

        if self._last_observed_index >= len(self.messages):
            return

        for msg in self.messages[self._last_observed_index :]:
            self.observer(msg)
        self._last_observed_index = len(self.messages)

    def clean_history(self) -> None:
        """Clean message history (remove invalid empty messages).

        Removes messages that have both None content and no tool calls.
        Also safeguards request validity by filling missing tool responses.
        """
        self.messages = [
            msg
            for msg in self.messages
            if msg.content is not None
            or (hasattr(msg, "tool_calls") and msg.tool_calls)
        ]

        ACCEPTABLE_HISTORY_SIZE = 2
        if len(self.messages) < ACCEPTABLE_HISTORY_SIZE:
            return

        self._fill_missing_tool_responses()
        self._ensure_assistant_after_tools()

    def _fill_missing_tool_responses(self) -> None:
        """Ensure every tool call has a corresponding tool response."""
        i = 1
        while i < len(self.messages):
            msg = self.messages[i]

            if msg.role == Role.assistant and msg.tool_calls:
                expected_responses = len(msg.tool_calls)

                if expected_responses > 0:
                    actual_responses = 0
                    j = i + 1
                    while j < len(self.messages) and self.messages[j].role == Role.tool:
                        actual_responses += 1
                        j += 1

                    if actual_responses < expected_responses:
                        insertion_point = i + 1 + actual_responses

                        for call_idx in range(actual_responses, expected_responses):
                            tool_call_data = msg.tool_calls[call_idx]

                            empty_response = LLMMessage(
                                role=Role.tool,
                                tool_call_id=tool_call_data.id or "",
                                name=(tool_call_data.function.name or "")
                                if tool_call_data.function
                                else "",
                                content=str(
                                    get_user_cancellation_message(
                                        CancellationReason.TOOL_NO_RESPONSE
                                    )
                                ),
                            )

                            self.messages.insert(insertion_point, empty_response)
                            insertion_point += 1

                    i = i + 1 + expected_responses
                    continue

            i += 1

    def _ensure_assistant_after_tools(self) -> None:
        """Ensure conversation ends with assistant message if last was tool."""
        MIN_MESSAGE_SIZE = 2
        if len(self.messages) < MIN_MESSAGE_SIZE:
            return

        last_msg = self.messages[-1]
        if last_msg.role is Role.tool:
            empty_assistant_msg = LLMMessage(role=Role.assistant, content="Understood.")
            self.messages.append(empty_assistant_msg)
