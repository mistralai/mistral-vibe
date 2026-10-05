"""The ``/status`` "Model & Provider" renderer: contract-exact literal text.

The assertions pin the exact markdown the contract specifies, including the
escaping that keeps dynamic values out of the markup parser.
"""

from __future__ import annotations

from typing import Any

from markdown_it import MarkdownIt

from vibe.app_server.provider_auth import ProviderAuthView
from vibe.cli.textual_ui.provider_auth_status import (
    _literal,
    render_provider_auth_section,
)

_API_BASE_MD = "https\\://api\\.anthropic\\.com/v1"


def _view(**kwargs: Any) -> ProviderAuthView:
    defaults: dict[str, Any] = {
        "model_display_name": "Claude Sonnet",
        "provider_name": "anthropic",
        "api_base": "https://api.anthropic.com/v1",
    }
    defaults.update(kwargs)
    return ProviderAuthView(**defaults)


def test_renders_model_provider_and_api_base() -> None:
    text = render_provider_auth_section(_view())

    assert text == (
        "## Model & Provider\n"
        "\n"
        f"- **Model**: Claude Sonnet\n"
        f"- **Provider**: anthropic\n"
        f"- **API base**: {_API_BASE_MD}"
    )


def test_invalid_api_base_without_displayable_value() -> None:
    text = render_provider_auth_section(_view(api_base=None))

    assert "- **API base**: Invalid API URL" in text


def test_control_characters_in_dynamic_values_render_as_one_line() -> None:
    """A newline in a display name must not start a new Markdown line, where
    it could inject a heading or a list item.
    """
    text = render_provider_auth_section(_view(model_display_name="Bad\n- **injected**"))

    assert "- **Model**: Bad - \\*\\*injected\\*\\*" in text
    assert "\n- **injected**" not in text


def test_c1_control_characters_render_as_a_space() -> None:
    # U+0085 (NEL) is a control character outside C0; it must not survive
    # into the rendered line either.
    text = render_provider_auth_section(_view(model_display_name="Bad\u0085Name"))

    assert "- **Model**: Bad Name" in text


def test_dynamic_values_render_literally_not_as_markup() -> None:
    # A provider named after markup must reach the screen as those characters.
    # The message renders through Textual Markdown, so the renderer escapes the
    # punctuation instead of leaving it for the parser.
    text = render_provider_auth_section(
        _view(model_display_name="Model [bold]", provider_name="not-a-tag")
    )

    assert "- **Model**: Model \\[bold\\]" in text
    assert "- **Provider**: not-a-tag" in text


def test_strikethrough_pairs_render_literally() -> None:
    # Textual parses the gfm-like preset, where ``~~`` opens strikethrough:
    # a display name carrying the pair must reach the screen as tildes, not
    # as struck-through text.
    text = render_provider_auth_section(_view(model_display_name="Model ~~x~~"))

    assert "- **Model**: Model \\~\\~x\\~\\~" in text


def test_escaped_values_leave_the_parser_nothing_to_structure() -> None:
    """The escaper's safety depends on the parser's feature set, so this pins
    the gfm-like preset Textual parses with: a value carrying every construct
    the preset understands must parse to plain text tokens only. A parser
    upgrade that grows the grammar fails here instead of rendering user text
    as structure.
    """
    parser = MarkdownIt("gfm-like")
    hostile = " ".join([
        "*em* **strong** `code`",
        "[link](https://evil.example)",
        "![image](https://evil.example)",
        "~~struck~~",
        "<b> &amp; #hash",
        "www.evil.example",
        "https://evil.example ftp://evil.example",
        "user@evil.example",
        "back\\_slash",
    ])

    tokens = parser.parse(f"prefix {_literal(hostile)}")

    inline = tokens[1]
    assert inline.type == "inline"
    assert inline.children is not None
    assert all(token.type == "text" for token in inline.children)
