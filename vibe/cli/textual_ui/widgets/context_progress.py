from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual.reactive import reactive

from vibe.cli.textual_ui.count_format import format_compact_count
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


@dataclass
class TokenState:
    max_tokens: int = 0
    current_tokens: int = 0


class ContextProgress(NoMarkupStatic):
    tokens = reactive(TokenState())

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def watch_tokens(self, new_state: TokenState) -> None:
        if new_state.max_tokens == 0:
            self.update("")
            return

        ratio = min(1, new_state.current_tokens / new_state.max_tokens)
        text = (
            f"{format_compact_count(new_state.current_tokens)}/"
            f"{format_compact_count(new_state.max_tokens)} tokens ({ratio:.0%})"
        )
        self.update(text)
