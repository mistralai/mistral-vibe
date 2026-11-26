from __future__ import annotations

import pytest

from vibe.app_server._skill_invocation import (
    invoked_skill_names,
    invoked_skill_payloads,
    mentioned_skill_names,
    public_session_preview,
    skill_invocation_display_names,
    with_skill_invocation_display,
    without_skill_invocation_display,
)
from vibe.core.tools.builtins.skill import already_loaded_message
from vibe.user_content import UserDisplayContent


def _body(name: str) -> str:
    return f'<skill_content name="{name}">\n# Skill: {name}\n\nDo it.\n</skill_content>'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/code-review please", ["code-review"]),
        ("please use /code-review", ["code-review"]),
        ("go /Code-Review, then /lint. And /code-review!", ["lint", "code-review"]),
        ("/code-review with /lint", ["code-review", "lint"]),
        ("/code-review with /code-review", ["code-review"]),
        ("go /vibe:skill-creator now", ["vibe:skill-creator"]),
        ("line one\n/lint on line two", ["lint"]),
        ("and/or a/lint", []),
        ("a lone / sign", []),
        ("please use $code-review and $lint", []),
        ("see /usr/bin, /Users/me and /Lint", []),
        ("run `/lint` or ``/lint`` inline", []),
        ("```sh\nrun /lint\n```\nthen /code-review", ["code-review"]),
        ("~~~\n/lint never closes", []),
        ("`x`/lint stays glued", []),
        ("mid-prompt /code-review is a mention", ["code-review"]),
    ],
)
def test_invoked_skill_names_reads_the_leading_command_and_slash_mentions(
    text: str, expected: list[str]
) -> None:
    assert invoked_skill_names(text) == expected


def test_mentioned_skill_names_ignores_the_leading_slash_command() -> None:
    assert mentioned_skill_names("/code-review with /lint") == ["lint"]
    assert mentioned_skill_names(" /code-review with /lint") == ["lint"]


def test_skill_invocation_display_round_trips_several_names() -> None:
    shown = UserDisplayContent(
        version="1", host="vibe", content=[{"type": "text", "text": "shown"}]
    )

    display = with_skill_invocation_display(shown, ["code-review", "lint"])

    assert skill_invocation_display_names(display) == ["code-review", "lint"]
    assert without_skill_invocation_display(display) == shown


def test_skill_invocation_display_without_names_keeps_the_display() -> None:
    assert with_skill_invocation_display(None, []) is None


def test_invoked_skill_payloads_finds_every_mentioned_body() -> None:
    texts = [
        "use /lint then /code-review",
        None,
        _body("lint"),
        already_loaded_message("code-review"),
        "scratchpad notes",
    ]

    payloads = invoked_skill_payloads(texts, ["lint", "code-review"])

    assert payloads == [(2, "lint"), (3, "code-review")]


def test_invoked_skill_payloads_ignores_a_body_the_prompt_does_not_invoke() -> None:
    texts = ["use /lint", _body("lint"), _body("code-review")]

    assert invoked_skill_payloads(texts, ["lint", "code-review"]) == [(1, "lint")]


def test_invoked_skill_payloads_requires_provenance_when_present() -> None:
    texts = ["use /lint", _body("lint")]

    assert invoked_skill_payloads(texts, ["code-review"]) == []


def test_invoked_skill_payloads_accepts_a_pre_provenance_slash_invocation() -> None:
    texts = ["/code-review please", _body("code-review")]

    assert invoked_skill_payloads(texts, []) == [(1, "code-review")]


def test_public_session_preview_preserves_a_bare_slash() -> None:
    assert public_session_preview("/") == "/"


def test_public_session_preview_drops_every_mentioned_body() -> None:
    preview = f"use /lint and /code-review {_body('lint')} {_body('code-review')}"

    assert public_session_preview(preview) == "use /lint and /code-review"


def test_public_session_preview_drops_a_truncated_body() -> None:
    preview = f"use /lint {_body('lint')[:20]}…"

    assert public_session_preview(preview) == "use /lint"
