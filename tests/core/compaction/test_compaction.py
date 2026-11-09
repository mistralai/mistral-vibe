from __future__ import annotations

from vibe.core.compaction import extract_summary, render_teleport_summary_request
from vibe.core.teleport.types import TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH


def test_extract_summary_returns_inner_block() -> None:
    assert extract_summary("<summary>hello there</summary>") == "hello there"


def test_extract_summary_strips_surrounding_text_and_whitespace() -> None:
    text = "preamble\n<summary>\n  the body\n</summary>\ntrailing"
    assert extract_summary(text) == "the body"


def test_extract_summary_missing_tags_returns_none() -> None:
    assert extract_summary("just some prose without tags") is None


def test_extract_summary_empty_block_returns_none() -> None:
    assert extract_summary("<summary>   </summary>") is None


def test_extract_summary_first_block_wins() -> None:
    assert extract_summary("<summary>one</summary><summary>two</summary>") == "one"


def test_extract_summary_preserves_inner_markup() -> None:
    assert extract_summary("<summary>a <b>c</b> d</summary>") == "a <b>c</b> d"


def test_teleport_summary_request_mentions_summary_limit() -> None:
    request = render_teleport_summary_request(
        "Summarize the session.",
        "Continue in Vibe Code Web.",
        max_summary_chars=TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH,
    )

    assert "under 8,000 characters" in request
    assert (
        "<teleported_prompt>\nContinue in Vibe Code Web.\n</teleported_prompt>"
        in request
    )
