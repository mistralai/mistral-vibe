from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any

from textual.reactive import reactive

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

_THOUSAND = 1_000
_MILLION = 1_000_000


@dataclass
class TokenState:
    max_tokens: int = 0
    current_tokens: int = 0
    session_cost: float = 0.0
    burn_rate_tokens_per_min: float = 0.0
    max_price: float = 0.0
    session_start_time: float = 0.0


def _format_token_count(tokens: int) -> str:
    if tokens >= _MILLION:
        return f"{tokens / _MILLION:.1f}M"
    if tokens >= _THOUSAND:
        return f"{tokens // _THOUSAND}k"
    return str(tokens)


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
            f"{_format_token_count(new_state.current_tokens)}/"
            f"{_format_token_count(new_state.max_tokens)} tokens ({ratio:.0%})"
        )
        self.update(text)

    async def _on_click(self, event: Any) -> None:
        handler = getattr(self.app, "_show_usage_monitor", None)
        if callable(handler):
            res = handler()
            if inspect.isawaitable(res):
                await res
