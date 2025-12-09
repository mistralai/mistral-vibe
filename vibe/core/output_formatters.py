from __future__ import annotations

from abc import ABC, abstractmethod
import json
import sys
from typing import TextIO

from vibe.core.types import AssistantEvent, BaseEvent, LLMMessage, OutputFormat


class OutputFormatter(ABC):
    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self.stream = stream
        self._messages: list[LLMMessage] = []
        self._final_response: str | None = None

    @abstractmethod
    def on_message_added(self, message: LLMMessage) -> None:
        pass

    @abstractmethod
    def on_event(self, event: BaseEvent) -> None:
        pass

    @abstractmethod
    def finalize(self) -> str | None:
        """Finalize output and return any final text to be printed.

        Returns:
            String to print, or None if formatter handles its own output
        """
        pass


class TextOutputFormatter(OutputFormatter):
    def on_message_added(self, message: LLMMessage) -> None:
        self._messages.append(message)

    def on_event(self, event: BaseEvent) -> None:
        if isinstance(event, AssistantEvent):
            self._final_response = event.content

    def finalize(self) -> str | None:
        return self._final_response


class JsonOutputFormatter(OutputFormatter):
    def on_message_added(self, message: LLMMessage) -> None:
        self._messages.append(message)

    def on_event(self, event: BaseEvent) -> None:
        pass

    def finalize(self) -> str | None:
        messages_data = [msg.model_dump(mode="json") for msg in self._messages]
        json.dump(messages_data, self.stream, indent=2)
        self.stream.write("\n")
        self.stream.flush()
        return None


class StreamingJsonOutputFormatter(OutputFormatter):
    def on_message_added(self, message: LLMMessage) -> None:
        json.dump(message.model_dump(mode="json"), self.stream)
        self.stream.write("\n")
        self.stream.flush()

    def on_event(self, event: BaseEvent) -> None:
        pass

    def finalize(self) -> str | None:
        return None


def create_formatter(
    format_type: OutputFormat, stream: TextIO = sys.stdout
) -> OutputFormatter:
    formatters = {
        OutputFormat.TEXT: TextOutputFormatter,
        OutputFormat.JSON: JsonOutputFormatter,
        OutputFormat.STREAMING: StreamingJsonOutputFormatter,
    }

    formatter_class = formatters.get(format_type, TextOutputFormatter)
    return formatter_class(stream)
