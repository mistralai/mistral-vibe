from __future__ import annotations

from vibe.core.utils.tokens import approx_token_count, truncate_middle_to_tokens

_MARKER = "\n\n[... truncated ...]\n\n"


def test_approx_token_count_empty() -> None:
    assert approx_token_count("") == 0


def test_approx_token_count_rounds_up() -> None:
    # 1..4 chars all round to 1 token; 5 rolls over to 2.
    assert approx_token_count("a") == 1
    assert approx_token_count("a" * 4) == 1
    assert approx_token_count("a" * 5) == 2


def test_approx_token_count_budget_boundary() -> None:
    # 80_000 chars == 20_000 tokens exactly; +1 char crosses the boundary.
    assert approx_token_count("a" * 80_000) == 20_000
    assert approx_token_count("a" * 80_001) == 20_001


def test_truncate_middle_returns_text_when_under_budget() -> None:
    assert truncate_middle_to_tokens("hello", 10) == "hello"


def test_truncate_middle_empty_inputs() -> None:
    assert truncate_middle_to_tokens("", 10) == ""
    assert truncate_middle_to_tokens("anything", 0) == ""
    assert truncate_middle_to_tokens("anything", -1) == ""


def test_truncate_middle_keeps_head_and_tail() -> None:
    text = "HEAD" + ("x" * 1000) + "TAIL"
    out = truncate_middle_to_tokens(text, max_tokens=20)  # 80 char budget
    assert _MARKER in out
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert len(out) <= 80


def test_truncate_middle_tight_budget_falls_back_to_head_cut() -> None:
    # Budget too tight to fit the marker (24 chars). Function head-truncates.
    text = "abcdefghijklmnopqrstuvwxyz"
    out = truncate_middle_to_tokens(text, max_tokens=2)  # 8 char budget
    assert _MARKER not in out
    assert out == "abcdefgh"
    assert len(out) == 8
