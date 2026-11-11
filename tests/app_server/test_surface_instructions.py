from __future__ import annotations

import re

import pytest

from vibe.app_server._surface_instructions import (
    ClientSurface,
    client_surface,
    with_surface_instructions,
)
from vibe.utils import AgentEntrypoint

pytest.importorskip("mistralai_vibe_local_harness.vibe")
from mistralai_vibe_local_harness.vibe import build_vibe_code_system_instructions

_VARIANTS = ["cli", "cli_2026-07_v2", "cli_2026-08_v3", "cli_ml4_2026-10_v2"]
_WITH_OVERRIDABLE_DEFAULTS = ["cli", "cli_2026-07_v2", "cli_ml4_2026-10_v2"]
_WITH_RESPONSE_FORMAT = {"cli", "cli_2026-07_v2"}
_HEADINGS: dict[ClientSurface, str] = {
    "cli": "Surface: Vibe CLI",
    "desktop": "Surface: Vibe Desktop",
    "neutral": "Surface: unspecified client",
}


def _prompt(variant: str, surface: ClientSurface) -> tuple[str, str]:
    common = build_vibe_code_system_instructions(variant=variant)
    return common, with_surface_instructions(common, variant=variant, surface=surface)


@pytest.mark.parametrize(
    ("entrypoint", "surface"),
    [
        ("cli", "cli"),
        ("desktop", "desktop"),
        ("acp", "neutral"),
        ("programmatic", "neutral"),
        ("unknown", "neutral"),
    ],
)
def test_each_client_gets_its_surface(
    entrypoint: AgentEntrypoint, surface: ClientSurface
) -> None:
    """*Prepare*: A client entrypoint.
    *Do*: Pick its surface.
    *Assert*: The terminal UI and Desktop get their own; every client whose
    rendering Vibe does not know gets the neutral one.
    """
    # Do
    picked = client_surface(entrypoint)

    # Assert
    assert picked == surface


@pytest.mark.parametrize("surface", sorted(_HEADINGS))
@pytest.mark.parametrize("variant", _VARIANTS)
def test_the_surface_block_follows_the_unchanged_product_prompt(
    variant: str, surface: ClientSurface
) -> None:
    """*Prepare*: Each SDK prompt variant.
    *Do*: Append the surface's block.
    *Assert*: The product prompt is kept as the SDK wrote it, followed by that
    surface's block only.
    """
    # Do
    common, instructions = _prompt(variant, surface)

    # Assert
    block = instructions.removeprefix(f"{common}\n\n")
    assert block != instructions
    assert re.match(rf"#+ {re.escape(_HEADINGS[surface])}\n", block)
    assert instructions.count("Surface: ") == 1


@pytest.mark.parametrize("surface", sorted(_HEADINGS))
@pytest.mark.parametrize("variant", _WITH_OVERRIDABLE_DEFAULTS)
def test_the_surface_block_nests_in_the_overridable_defaults(
    variant: str, surface: ClientSurface
) -> None:
    """*Prepare*: Each variant whose last section is its overridable defaults.
    *Do*: Append the surface's block.
    *Assert*: The block is a subsection of those defaults, so user prompts,
    AGENTS.md files, and skills rank against it as they did against the
    response format it carries.
    """
    # Do
    _, instructions = _prompt(variant, surface)

    # Assert
    tail = instructions[instructions.index("## Overridable defaults") :]
    assert f"\n### {_HEADINGS[surface]}\n" in tail
    assert "\n## " not in tail.removeprefix("## Overridable defaults")


@pytest.mark.parametrize("variant", _VARIANTS)
def test_the_cli_block_adds_no_rule_the_variant_never_had(variant: str) -> None:
    """*Prepare*: Each variant, with or without its own response format.
    *Do*: Append the CLI block.
    *Assert*: Every variant says its output is shown in a terminal; only a
    variant that carried the terminal response format keeps it, so no
    experiment arm gains formatting guidance.
    """
    # Do
    _, instructions = _prompt(variant, "cli")

    # Assert
    has_response_format = variant in _WITH_RESPONSE_FORMAT
    assert instructions.count("GitHub-flavored markdown in a terminal") == 1
    assert ("**Response format.** Structure first." in instructions) is (
        has_response_format
    )
    assert ("- Tree / hierarchy → `├── └──`" in instructions) is has_response_format
    assert (
        "- Code reference → `path/to/file.py:42` then a fenced block" in instructions
    ) is has_response_format


@pytest.mark.parametrize("variant", _VARIANTS)
def test_the_desktop_block_formats_for_the_rendered_chat_panel(variant: str) -> None:
    """*Prepare*: Each variant.
    *Do*: Append the Desktop block.
    *Assert*: It says the output is rendered markdown in a proportional font,
    never asks for box-drawing trees, warns about what does not render, and
    points to the views that already show tool activity.
    """
    # Do
    _, instructions = _prompt(variant, "desktop")

    # Assert
    assert "in a desktop chat panel with a proportional font, not in a terminal" in (
        instructions
    )
    assert "`├── └──`" not in instructions
    assert "never use box-drawing characters in prose" in instructions
    assert "fenced `mermaid` flowchart" in instructions
    assert "bare `$…$` renders as math" in instructions
    assert "Raw HTML (except `<br>`) shows as literal text" in instructions
    assert "the todo list have their own views" in instructions


@pytest.mark.parametrize("variant", _VARIANTS)
def test_the_neutral_block_claims_no_terminal_and_no_renderer(variant: str) -> None:
    """*Prepare*: Each variant, with or without its own response format.
    *Do*: Append the neutral block.
    *Assert*: It names neither a terminal nor a product client, asks for
    portable markdown, and only a variant with a response format keeps one,
    without box-drawing trees.
    """
    # Do
    common, instructions = _prompt(variant, "neutral")

    # Assert
    block = instructions.removeprefix(f"{common}\n\n")
    has_response_format = variant in _WITH_RESPONSE_FORMAT
    assert "Write portable GitHub-flavored markdown" in block
    assert "in a terminal" not in block
    assert "Vibe CLI" not in block
    assert "Vibe Desktop" not in block
    assert "├──" not in block
    assert ("**Response format.** Structure first." in block) is has_response_format
    assert ("- Hierarchy → nested list" in block) is has_response_format


def test_every_sdk_variant_has_a_layout() -> None:
    """*Prepare*: The prompt variants the harness SDK ships, and the layouts
    the app server knows.
    *Do*: Compare the two sets.
    *Assert*: They match, so a variant added to the SDK cannot silently take
    the default layout and gain rules it never carried.
    """
    # Prepare
    from mistralai_vibe_local_harness.vibe._system_instructions import (
        _SYSTEM_INSTRUCTIONS_TEMPLATES,
    )
    from vibe.app_server._surface_instructions import _VARIANT_LAYOUTS

    # Do
    sdk_variants = set(_SYSTEM_INSTRUCTIONS_TEMPLATES)
    known_variants = set(_VARIANT_LAYOUTS)

    # Assert
    assert known_variants == sdk_variants


def test_an_unknown_variant_takes_the_default_layout() -> None:
    """*Prepare*: A variant name the app server has no layout for.
    *Do*: Append the CLI block to the SDK's prompt for it.
    *Assert*: It gets the default variant's block, as the SDK falls back to
    its default variant's text.
    """
    # Do
    _, unknown = _prompt("retired_variant", "cli")
    _, default = _prompt("cli", "cli")

    # Assert
    assert unknown == default
