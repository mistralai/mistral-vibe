"""Renders the ``/status`` "Model & Provider" section from the public view.

The view arrives redacted from the app server; this module only turns it into
the literal text the contract specifies. The message renders through Textual
``Markdown``, so every dynamic value is backslash-escaped first: a provider
named ``[bold]`` must reach the screen as those characters, not as markup
(ADR 0010).
"""

from __future__ import annotations

import unicodedata

from vibe.app_server.provider_auth import ProviderAuthView

__all__ = ["render_provider_auth_section"]

# Characters that change Markdown inline parsing when left bare. ``.`` and
# ``:`` are escaped although a backslash before them renders identically:
# the gfm-like preset Textual parses with has linkify on, and they are what
# lets a bare URL in a dynamic value open a link. ``-`` ``+`` need no
# escaping: they only matter at the start of a line (list markers), and the
# control-character filter below keeps a dynamic value from ever starting
# one. ``~`` is handled by adjacency in ``_literal`` instead: a lone tilde
# is literal, and escaping every tilde would deface a path-like value.
_MD_INLINE = frozenset("\\`*_.:<>&!#[]")


def _literal(value: str) -> str:
    """Escape markdown punctuation so a dynamic value renders literally.

    Control characters (C0, DEL, and the C1 range) have no literal form
    inside a one-line field — a newline would start a new Markdown line and
    could inject structure — so they render as a space.
    """
    value = "".join(
        " " if unicodedata.category(char) == "Cc" else char for char in value
    )
    parts: list[str] = []
    for index, char in enumerate(value):
        # Strikethrough under the gfm-like preset Textual parses with needs a
        # ``~~`` pair, so a tilde is escaped only next to another one.
        if char == "~" and (
            value[index - 1 : index] == "~" or value[index + 1 : index + 2] == "~"
        ):
            parts.append("\\~")
        elif char in _MD_INLINE:
            parts.append("\\" + char)
        else:
            parts.append(char)
    return "".join(parts)


def _render_api_base(view: ProviderAuthView) -> str:
    if view.api_base is not None:
        return f"- **API base**: {_literal(view.api_base)}"
    return "- **API base**: Invalid API URL"


def render_provider_auth_section(view: ProviderAuthView) -> str:
    lines = ["## Model & Provider", ""]
    lines.append(f"- **Model**: {_literal(view.model_display_name)}")
    lines.append(f"- **Provider**: {_literal(view.provider_name)}")
    lines.append(_render_api_base(view))
    return "\n".join(lines)
