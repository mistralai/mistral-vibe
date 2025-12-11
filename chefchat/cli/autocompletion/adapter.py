from __future__ import annotations

from collections.abc import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from chefchat.core.autocompletion.completers import Completer as VibeCompleter


class PromptToolkitCompleterAdapter(Completer):
    """Adapter to make Vibe Completers compatible with prompt_toolkit."""

    def __init__(self, completers: list[VibeCompleter]) -> None:
        self.completers = completers

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text
        cursor_pos = document.cursor_position

        for completer in self.completers:
            # Check if this completer wants to handle this context
            repl_range = completer.get_replacement_range(text, cursor_pos)
            if repl_range is None:
                continue

            start, end = repl_range

            # Get completion items (text, description)
            items = completer.get_completion_items(text, cursor_pos)

            for completion_text, description in items:
                # Calculate the start position relative to the cursor
                # prompt_toolkit expects start_position to be negative (characters before cursor)
                start_position = start - cursor_pos

                yield Completion(
                    text=completion_text,
                    start_position=start_position,
                    display=completion_text,
                    display_meta=description,
                )
