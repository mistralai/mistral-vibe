from __future__ import annotations

from typing import Any
from urllib.parse import quote, unquote, urlsplit

from rich.highlighter import ReprHighlighter
from rich.text import Text
from textual.content import Content, ContentType
from textual.style import Style
from textual.widgets import Static

from vibe.observability.logging import logger

_SAFE_SCHEMES = {"http", "https"}

# Rich's repr highlighter tags URL spans with the style name "repr.url".
# This is a stable public-ish style key (used by Rich's pretty printer) but
# we depend on it implicitly — if Rich renames it, linkify silently no-ops.
_URL_HIGHLIGHTER = ReprHighlighter()
_URL_STYLE = "repr.url"

# Codepoints below this (C0 controls) and DEL are illegal in a URL.
_FIRST_PRINTABLE_CODEPOINT = 0x20
_DELETE_CHAR = "\x7f"


def _is_safe_url(url: str) -> bool:
    return urlsplit(url).scheme.lower() in _SAFE_SCHEMES


def normalize_url(url: str) -> str:
    """Percent-encode URL-illegal characters (backslash, control chars) that can
    slip in from model output and break the platform browser opener.
    """
    out: list[str] = []
    for ch in url:
        if ch == "\\":
            out.append("%5C")
        elif ord(ch) < _FIRST_PRINTABLE_CODEPOINT or ch == _DELETE_CHAR:
            out.append(f"%{ord(ch):02X}")
        else:
            out.append(ch)
    return "".join(out)


def _click_style(url: str) -> Style:
    # Percent-encode so brackets/quotes/parens in the URL can't break the
    # @click action literal; action_open_url decodes it.
    return Style.from_meta({"@click": f"open_url('{quote(url, safe='')}')"})


def link_content(label: str, url: str) -> Content:
    content = Content(label)
    if _is_safe_url(url):
        return content.stylize(_click_style(url))
    return content


def linkify_urls_in_text(text: str) -> Content:
    rich = Text(text)
    _URL_HIGHLIGHTER.highlight(rich)
    content = Content(text)
    for span in rich.spans:
        if span.style != _URL_STYLE:
            continue
        url = text[span.start : span.end]
        if _is_safe_url(url):
            content = content.stylize(_click_style(url), span.start, span.end)
    return content


class LinkStatic(Static):
    def __init__(self, content: ContentType = "", **kwargs: Any) -> None:
        # markup=False: plain strings render literally and Content objects carry
        # their own styling/meta, so user text containing brackets cannot crash.
        super().__init__(content, markup=False, **kwargs)

    def action_open_url(self, url: str) -> None:
        target = unquote(url)
        if not _is_safe_url(target):
            logger.warning("Refusing to open url=%s", target)
            return
        self.app.open_url(target)
        # Hover highlight only refreshes on mouse move, so re-render to keep the
        # link styled after a click.
        self.refresh()
