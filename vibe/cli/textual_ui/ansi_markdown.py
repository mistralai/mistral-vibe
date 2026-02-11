from __future__ import annotations

from pygments.token import Token
from textual.content import Content
from textual.highlight import HighlightTheme, highlight
from textual.widgets import Markdown
from textual.widgets._markdown import MarkdownFence


class AnsiHighlightTheme(HighlightTheme):
    STYLES = {
        Token.Comment: "ansi_bright_black italic",
        Token.Error: "ansi_red",
        Token.Generic.Strong: "bold",
        Token.Generic.Emph: "italic",
        Token.Generic.Error: "ansi_red",
        Token.Generic.Heading: "ansi_blue underline",
        Token.Generic.Subheading: "ansi_blue",
        Token.Keyword: "ansi_magenta",
        Token.Keyword.Constant: "ansi_cyan",
        Token.Keyword.Namespace: "ansi_magenta",
        Token.Keyword.Type: "ansi_cyan",
        Token.Literal.Number: "ansi_yellow",
        Token.Literal.String.Backtick: "ansi_bright_black",
        Token.Literal.String: "ansi_green",
        Token.Literal.String.Doc: "ansi_green italic",
        Token.Literal.String.Double: "ansi_green",
        Token.Name: "ansi_default",
        Token.Name.Attribute: "ansi_yellow",
        Token.Name.Builtin: "ansi_cyan",
        Token.Name.Builtin.Pseudo: "italic",
        Token.Name.Class: "ansi_yellow",
        Token.Name.Constant: "ansi_red",
        Token.Name.Decorator: "ansi_blue",
        Token.Name.Function: "ansi_blue",
        Token.Name.Function.Magic: "ansi_blue",
        Token.Name.Tag: "ansi_blue",
        Token.Name.Variable: "ansi_default",
        Token.Number: "ansi_yellow",
        Token.Operator: "ansi_default",
        Token.Operator.Word: "ansi_magenta",
        Token.String: "ansi_green",
        Token.Whitespace: "",
    }


class AnsiMarkdownFence(MarkdownFence):
    @classmethod
    def highlight(cls, code: str, language: str) -> Content:
        return highlight(code, language=language or None, theme=AnsiHighlightTheme)


class AnsiMarkdown(Markdown):
    BLOCKS = {
        **Markdown.BLOCKS,
        "fence": AnsiMarkdownFence,
        "code_block": AnsiMarkdownFence,
    }
