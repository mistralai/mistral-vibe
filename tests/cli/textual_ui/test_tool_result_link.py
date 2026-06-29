from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vibe.cli.textual_ui.widgets.links import (
    LinkStatic,
    link_content,
    linkify_urls_in_text,
)
from vibe.cli.textual_ui.widgets.tool_widgets import WebSearchResultWidget
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage
from vibe.core.tools.builtins.websearch import WebSearchResult, WebSearchSource


def _click_actions(content: object) -> list[str]:
    spans = getattr(content, "spans", [])
    return [
        span.style.meta["@click"]
        for span in spans
        if span.style.meta and "@click" in span.style.meta
    ]


def test_link_content_encodes_url_in_action_and_keeps_label() -> None:
    # The action arg is percent-encoded; the visible label is the page name.
    content = link_content("Example", "https://example.com")
    assert content.plain == "Example"
    assert _click_actions(content) == ["open_url('https%3A%2F%2Fexample.com')"]


def test_link_content_only_links_http_schemes() -> None:
    # Non-http(s) schemes render as the plain label, with no clickable @click span.
    for url in ("file:///etc/passwd", "javascript:alert(1)", "vscode://x"):
        content = link_content(url, url)
        assert content.plain == url
        assert _click_actions(content) == []


def test_link_content_handles_previously_unsafe_urls() -> None:
    # Brackets, quotes, parens live in the encoded action, never in the label text.
    for url in (
        "https://e.org/x[1]",
        "https://e.org/it's",
        "https://e.org/x)",
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
    ):
        content = link_content(url, url)
        assert content.plain == url  # literal text, never re-parsed as markup
        assert len(_click_actions(content)) == 1


class _Harness(App):
    def __init__(self, url: str = "https://example.com") -> None:
        super().__init__()
        self.url = url
        self.opened: list[str] = []

    def compose(self) -> ComposeResult:
        yield LinkStatic(link_content(self.url, self.url))

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
    content = linkify_urls_in_text("Fetched https://example.com (10 chars, text/html)")
    assert content.plain == "Fetched https://example.com (10 chars, text/html)"
    assert _click_actions(content) == ["open_url('https%3A%2F%2Fexample.com')"]


def test_linkify_urls_in_text_handles_multiple_urls() -> None:
    content = linkify_urls_in_text("see https://a.com and https://b.com here")
    assert _click_actions(content) == [
        "open_url('https%3A%2F%2Fa.com')",
        "open_url('https%3A%2F%2Fb.com')",
    ]


def test_linkify_urls_in_text_keeps_balanced_parens_in_url() -> None:
    # Wikipedia-style URLs with `(…)` were the reason the @click action is
    # percent-encoded; Rich's URL detector already keeps them in the span.
    url = "https://en.wikipedia.org/wiki/Python_(programming_language)"
    content = linkify_urls_in_text(f"see {url} for details")
    assert url in content.plain
    assert _click_actions(content) == [
        "open_url('https%3A%2F%2Fen.wikipedia.org%2Fwiki%2F"
        "Python_%28programming_language%29')"
    ]


def test_linkify_urls_in_text_keeps_brackets_literal_when_no_url() -> None:
    # Raw tool text with brackets stays literal (Content is never markup-parsed).
    content = linkify_urls_in_text("Searched '[a]' (2 sources)")
    assert content.plain == "Searched '[a]' (2 sources)"
    assert _click_actions(content) == []


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
async def test_tool_call_message_set_result_text_keeps_brackets_literal_off() -> None:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("linkify", [False, True])
async def test_tool_call_message_renders_malformed_markup_without_crashing(
    linkify: bool,
) -> None:
    # A bash summary like `git tag [/foo bar]` reads as a broken Rich closing
    # tag and used to raise MarkupError when rendered into a markup=True widget.
    call = ToolCallMessage(tool_name="bash")

    class _H(App):
        def compose(self) -> ComposeResult:
            yield call

    text = "ran: git tag [/foo bar] && echo [done] https://example.com"
    async with _H().run_test() as pilot:
        await pilot.pause(0.1)
        call.set_result_text(text, linkify=linkify)
        await pilot.pause(0.1)
        rendered = str(call._text_widget.render()) if call._text_widget else ""

    assert "[/foo bar]" in rendered
    assert "[done]" in rendered
