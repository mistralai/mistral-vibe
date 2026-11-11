"""The part of the Vibe Code prompt that depends on the client.

The harness SDK owns each prompt variant's text and keeps it free of anything
client-specific. The app server knows which client opened the session, so it
appends the block that says where the model's text is displayed and how to
format it there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vibe.utils import AgentEntrypoint

type ClientSurface = Literal["cli", "desktop", "neutral"]


def client_surface(entrypoint: AgentEntrypoint) -> ClientSurface:
    """The surface whose block a session's prompt ends with.

    The terminal UI and Vibe Desktop each get their own. ACP editors, headless
    runs and unidentified clients render the text in ways Vibe does not know,
    so they get the neutral block. Core holds its prompt for the Session's
    life, so a session resumed on another surface keeps the block it started
    with.
    """
    match entrypoint:
        case "cli":
            return "cli"
        case "desktop":
            return "desktop"
        case "acp" | "programmatic" | "unknown":
            return "neutral"


def with_surface_instructions(
    instructions: str, *, variant: str, surface: ClientSurface
) -> str:
    """Append ``surface``'s block to the product prompt of ``variant``."""
    layout = _VARIANT_LAYOUTS.get(variant, _DEFAULT_LAYOUT)
    return f"{instructions}\n\n{_surface_block(surface, layout)}"


@dataclass(frozen=True, slots=True)
class _VariantLayout:
    """How a prompt variant takes its surface block.

    ``heading_level`` nests the block in the variant's own outline: inside its
    overridable defaults where it has them, so the block keeps their rank.
    ``carries_response_format`` is whether the variant asks for a response
    format ("Structure first") and the surface's formatting rules that come
    with it. A variant that never asked for them does not gain them. Desktop's
    rendering rules describe what the chat panel can show, so every variant
    gets them there.
    """

    heading_level: int
    carries_response_format: bool


_DEFAULT_LAYOUT = _VariantLayout(heading_level=3, carries_response_format=True)

# Keyed by the harness SDK's variant names; a test keeps the two sets equal.
# A variant name neither knows takes the default layout, as the SDK falls back
# to its default variant's text.
_VARIANT_LAYOUTS: dict[str, _VariantLayout] = {
    "cli": _DEFAULT_LAYOUT,
    "cli_2026-07_v2": _DEFAULT_LAYOUT,
    "cli_2026-08_v3": _VariantLayout(heading_level=1, carries_response_format=False),
    "cli_ml4_2026-10_v2": _VariantLayout(
        heading_level=3, carries_response_format=False
    ),
}

_HEADINGS: dict[ClientSurface, str] = {
    "cli": "Surface: Vibe CLI",
    "desktop": "Surface: Vibe Desktop",
    "neutral": "Surface: unspecified client",
}

_DISPLAY: dict[ClientSurface, str] = {
    "cli": (
        "Text you output outside of tool use is displayed to the user as"
        " GitHub-flavored markdown in a terminal."
    ),
    "desktop": (
        "Text you output outside of tool use is rendered as GitHub-flavored"
        " markdown in a desktop chat panel with a proportional font, not in a"
        " terminal."
    ),
    "neutral": (
        "Text you output outside of tool use is displayed by a client whose"
        " rendering is unknown: an editor panel, a terminal, or another program."
        " Write portable GitHub-flavored markdown that reads well rendered or as"
        " plain text."
    ),
}

_RESPONSE_FORMAT_OPENING = (
    "**Response format.** Structure first. Prose after, if at all."
)

_RESPONSE_FORMATS: dict[ClientSurface, str] = {
    "cli": f"""\
{_RESPONSE_FORMAT_OPENING}

- Tree / hierarchy → `├── └──`
- Comparison / options → markdown table
- Flow → `A → B → C`
- Code reference → `path/to/file.py:42` then a fenced block""",
    "desktop": _RESPONSE_FORMAT_OPENING,
    "neutral": f"""\
{_RESPONSE_FORMAT_OPENING}

- Hierarchy → nested list
- Comparison / options → markdown table
- Flow → `A → B → C`
- Code reference → `path/to/file.py:42` then a fenced block""",
}

_DESKTOP_RENDERING = """\
- Comparison / options → markdown table.
- Hierarchy → nested list. Put a file tree in a fenced `text` block; never use box-drawing characters in prose.
- Flow → `A → B → C`. Only when a flow is too complex for one line, use a fenced `mermaid` flowchart; invalid syntax shows an error instead of a diagram.
- Code reference → `path/to/file.py:42`, then a fenced block with a language tag when the code matters.
- Put shell variables and other `$` text in inline code: bare `$…$` renders as math.
- Raw HTML (except `<br>`) shows as literal text. Do not use it.

Diffs, command output, tool calls, and the todo list have their own views. Do not restate them: quote only the lines that matter, and say what changed and why."""


def _surface_block(surface: ClientSurface, layout: _VariantLayout) -> str:
    sections = [f"{'#' * layout.heading_level} {_HEADINGS[surface]}", _DISPLAY[surface]]
    if layout.carries_response_format:
        sections.append(_RESPONSE_FORMATS[surface])
    if surface == "desktop":
        sections.append(_DESKTOP_RENDERING)
    return "\n\n".join(sections)
