from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vibe.cli.textual_ui.widgets.links import (
    LinkStatic,
    link_markup,
    linkify_urls_in_text,
)
from vibe.cli.textual_ui.widgets.tool_widgets import WebSearchResultWidget
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage
from vibe.core.tools.builtins.websearch import WebSearchResult, WebSearchSource


def test_link_markup_encodes_url_in_action_and_renders_label() -> None:
    # The action arg is percent-encoded; the visible label is the page name.
    assert (
        link_markup("Example", "https://example.com")
        == "[@click=open_url('https%3A%2F%2Fexample.com')]Example[/]"
    )


def test_link_markup_only_links_http_schemes() -> None:
    # Non-http(s) schemes render as the plain label, not a clickable @click span.
    for url in ("file:///etc/passwd", "javascript:alert(1)", "vscode://x"):
        assert link_markup(url, url) == url
        assert "@click" not in link_markup(url, url)


def test_link_markup_handles_previously_unsafe_urls() -> None:
    # Brackets, quotes, parens — all encoded into the action, never a fallback.
    for url in (
        "https://e.org/x[1]",
        "https://e.org/it's",
        "https://e.org/x)",
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
    ):
        markup = link_markup(url, url)
        assert markup.startswith("[@click=open_url('")
        assert "[1]" not in markup.split("]", 1)[0]  # raw bracket not in the tag


class _Harness(App):
    def __init__(self, url: str = "https://example.com") -> None:
        super().__init__()
        self.url = url
        self.opened: list[str] = []

    def compose(self) -> ComposeResult:
        yield LinkStatic(link_markup(self.url, self.url))

    def open_url(self, url: str, *, new_tab: bool = True) -> None:
        self.opened.append(url)


@pytest.mark.asyncio
async def test_clicking_link_span_opens_url() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await pilot.click(LinkStatic)
        await pilot.pause(0.1)

    assert app.opened == ["https://example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
        "https://httpbin.org/anything/test[1]",
        "https://e.org/it's",
    ],
)
async def test_clicking_decodes_back_to_original_url(url: str) -> None:
    app = _Harness(url)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await pilot.click(LinkStatic)
        await pilot.pause(0.1)

    assert app.opened == [url]


@pytest.mark.asyncio
async def test_action_open_url_ignores_non_http_scheme() -> None:
    from urllib.parse import quote

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app.query_one(LinkStatic).action_open_url(quote("file:///etc/passwd", safe=""))
        await pilot.pause(0.1)

    assert app.opened == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["javascript", "file", "data"])
async def test_unsafe_schemes_are_rejected(scheme: str) -> None:
    app = _Harness(f"{scheme}:payload")
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await pilot.click(LinkStatic)
        await pilot.pause(0.1)

    assert app.opened == []


def test_linkify_urls_in_text_auto_detects_url() -> None:
    # Once a tool is opted into linkification, URLs are found in the message
    # itself — the call site doesn't have to point at the URL span.
    markup = linkify_urls_in_text("Fetched https://example.com (10 chars, text/html)")
    assert markup.startswith("Fetched ")
    assert (
        "[@click=open_url('https%3A%2F%2Fexample.com')]https://example.com[/]" in markup
    )


def test_linkify_urls_in_text_handles_multiple_urls() -> None:
    markup = linkify_urls_in_text("see https://a.com and https://b.com here")
    assert "[@click=open_url('https%3A%2F%2Fa.com')]https://a.com[/]" in markup
    assert "[@click=open_url('https%3A%2F%2Fb.com')]https://b.com[/]" in markup


def test_linkify_urls_in_text_keeps_balanced_parens_in_url() -> None:
    # Wikipedia-style URLs with `(…)` were the reason the @click action is
    # percent-encoded; Rich's URL detector already keeps them in the span.
    url = "https://en.wikipedia.org/wiki/Python_(programming_language)"
    markup = linkify_urls_in_text(f"see {url} for details")
    assert link_markup(url, url) in markup


def test_linkify_urls_in_text_escapes_and_keeps_plain_when_no_url() -> None:
    # Brackets must be escaped so raw tool text can't break the markup.
    assert (
        linkify_urls_in_text("Searched '[a]' (2 sources)")
        == "Searched '\\[a]' (2 sources)"
    )


async def _rendered_lines(widget: WebSearchResultWidget) -> list[str]:
    from textual.widgets import Static

    class _H(App):
        def compose(self) -> ComposeResult:
            yield widget

    async with _H().run_test() as pilot:
        await pilot.pause(0.1)
        return [str(w.render()) for w in widget.query(Static)]


@pytest.mark.asyncio
async def test_websearch_single_source_is_bulleted_without_header() -> None:
    result = WebSearchResult(
        query="uv",
        answer="ans",
        sources=[WebSearchSource(title="Docs", url="https://x.com")],
    )
    lines = await _rendered_lines(
        WebSearchResultWidget(result, success=True, message="m")
    )
    joined = "\n".join(lines)

    assert "• Docs" in joined  # bulleted, page name as the link label
    assert "https://x.com" not in joined  # url lives in the @click action, not text
    assert "Source:" not in joined  # singular "Source:" prefix dropped
    assert "Sources:" not in joined  # no header for a lone source
    assert "WebSearchSource(" not in joined  # raw `sources: [...]` dump dropped


@pytest.mark.asyncio
async def test_websearch_multiple_sources_is_bulleted_plural() -> None:
    result = WebSearchResult(
        query="uv",
        answer="ans",
        sources=[
            WebSearchSource(title="A", url="https://a.com"),
            WebSearchSource(title="B", url="https://b.com"),
        ],
    )
    lines = await _rendered_lines(
        WebSearchResultWidget(result, success=True, message="m")
    )
    joined = "\n".join(lines)

    assert "Sources:" in joined
    assert "• A" in joined
    assert "• B" in joined
    assert "https://a.com" not in joined  # url lives in the @click action, not text
    assert "WebSearchSource(" not in joined


@pytest.mark.asyncio
async def test_tool_call_message_set_result_text_renders_clickable_url() -> None:
    call = ToolCallMessage(tool_name="web_fetch")

    class _H(App):
        def compose(self) -> ComposeResult:
            yield call

    async with _H().run_test() as pilot:
        await pilot.pause(0.1)
        call.set_result_text(
            "Fetched https://example.com (10 chars, text/html)", linkify=True
        )
        await pilot.pause(0.1)
        rendered = str(call._text_widget.render()) if call._text_widget else ""

    # The URL becomes a clickable span in the status line; surrounding text stays.
    assert "https://example.com" in rendered
    assert "Fetched" in rendered


@pytest.mark.asyncio
async def test_tool_call_message_set_result_text_escapes_when_linkify_off() -> None:
    call = ToolCallMessage(tool_name="bash")

    class _H(App):
        def compose(self) -> ComposeResult:
            yield call

    async with _H().run_test() as pilot:
        await pilot.pause(0.1)
        # Bash isn't in the linkify whitelist; URLs must stay plain text and
        # brackets in the message must not be interpreted as markup.
        call.set_result_text("ran: see https://example.com [exit 0]")
        await pilot.pause(0.1)
        rendered = str(call._text_widget.render()) if call._text_widget else ""

    assert "@click=open_url" not in rendered
    assert "https://example.com" in rendered
    assert "[exit 0]" in rendered
