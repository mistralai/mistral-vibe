from __future__ import annotations

from typing import Any
from urllib.parse import quote, unquote, urlsplit

from rich.highlighter import ReprHighlighter
from rich.markup import escape
from rich.text import Text
from textual.widgets import Static

from vibe.core.logger import logger

_SAFE_SCHEMES = {"http", "https"}

# Rich's repr highlighter tags URL spans with the style name "repr.url".
# This is a stable public-ish style key (used by Rich's pretty printer) but
# we depend on it implicitly — if Rich renames it, linkify silently no-ops.
_URL_HIGHLIGHTER = ReprHighlighter()
_URL_STYLE = "repr.url"


def _is_safe_url(url: str) -> bool:
    return urlsplit(url).scheme.lower() in _SAFE_SCHEMES


def link_markup(label: str, url: str) -> str:
    # Only http(s) URLs become clickable; other schemes (file:, custom protocol
    # handlers, ...) render as plain text so an untrusted source URL can't steer
    # a click to a local handler. Percent-encode so brackets/quotes/parens can't
    # break the markup or the @click action literal; action_open_url decodes it.
    if not _is_safe_url(url):
        return escape(label)
    return f"[@click=open_url('{quote(url, safe='')}')]{escape(label)}[/]"


def linkify_urls_in_text(text: str) -> str:
    rich = Text(text)
    _URL_HIGHLIGHTER.highlight(rich)
    spans = sorted(
        (s for s in rich.spans if s.style == _URL_STYLE), key=lambda s: s.start
    )
    if not spans:
        return escape(text)
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(escape(text[cursor : span.start]))
        url = text[span.start : span.end]
        parts.append(link_markup(url, url))
        cursor = span.end
    parts.append(escape(text[cursor:]))
    return "".join(parts)


class LinkStatic(Static):
    def __init__(self, content: str = "", **kwargs: Any) -> None:
        super().__init__(content, markup=True, **kwargs)

    def action_open_url(self, url: str) -> None:
        target = unquote(url)
        if not _is_safe_url(target):
            logger.warning("Refusing to open url=%s", target)
            return
        self.app.open_url(target)
        # Hover highlight only refreshes on mouse move, so re-render to keep the
        # link styled after a click.
        self.refresh()
