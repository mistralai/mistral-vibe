from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual.reactive import reactive

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

_THOUSAND = 1_000
_MILLION = 1_000_000

_KIBI = 1024
_MEBI = 1024 * 1024
_GIBI = 1024 * 1024 * 1024


def _format_token_count(tokens: int) -> str:
    if tokens >= _MILLION:
        return f"{tokens / _MILLION:.1f}M"
    if tokens >= _THOUSAND:
        return f"{tokens // _THOUSAND}k"
    return str(tokens)


def _format_memory_size(size_bytes: int) -> str:
    if size_bytes >= _GIBI:
        return f"{size_bytes / _GIBI:.1f}G"
    if size_bytes >= _MEBI:
        return f"{size_bytes / _MEBI:.1f}M"
    if size_bytes >= _KIBI:
        return f"{size_bytes / _KIBI:.1f}K"
    return f"{size_bytes}B"


@dataclass
class TokenState:
    max_tokens: int = 0
    current_tokens: int = 0
    model_name: str = ""
    session_cost: float = 0.0
    memory_bytes: int = 0
    total_memory_bytes: int = 0


def _get_total_memory() -> int:
    """Return total RSS of this process + all direct children in bytes.

    Uses ``psutil`` when available; falls back to the main process only
    (same as ``memory_bytes``) when psutil is not installed.
    """
    try:
        import psutil
    except Exception:
        try:
            import resource
            import sys

            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return rss if sys.platform == "darwin" else rss * 1024
        except Exception:
            return 0

    try:
        parent = psutil.Process()
        children = parent.children(recursive=False)
        total = parent.memory_info().rss
        for child in children:
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total
    except Exception:
        return 0


class ContextProgress(NoMarkupStatic):
    tokens = reactive(TokenState())

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def watch_tokens(self, new_state: TokenState) -> None:
        if new_state.max_tokens == 0:
            self.update("")
            return

        ratio = min(1, new_state.current_tokens / new_state.max_tokens)
        parts = [
            f"{_format_token_count(new_state.current_tokens)}/"
            f"{_format_token_count(new_state.max_tokens)} tokens ({ratio:.0%})"
        ]
        if new_state.model_name:
            parts.append(f"[{new_state.model_name}]")
        if new_state.session_cost > 0:
            parts.append(f"${new_state.session_cost:.2f}")
        if new_state.memory_bytes > 0 or new_state.total_memory_bytes > 0:
            mem_parts = []
            if new_state.memory_bytes > 0:
                mem_parts.append(_format_memory_size(new_state.memory_bytes))
            if new_state.total_memory_bytes > 0:
                mem_parts.append(_format_memory_size(new_state.total_memory_bytes))
            parts.append(mem_parts[0])
        self.update(" | ".join(parts))
