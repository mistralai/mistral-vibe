from __future__ import annotations

import pytest

from vibe.core.git.worktree.naming import (
    MAX_WORKTREE_NAME_LENGTH,
    worktree_name_from_text,
    worktree_name_with_suffix,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Add the Git worktree selector to Vibe Desktop!",
            "add-the-git-worktree-selector",
        ),
        (
            "work something random to demonstrate the new auto worktree naming",
            "work-something-random-to-demonstrate",
        ),
        ("Fix the checkout flow crash on Safari", "fix-the-checkout-flow-crash"),
        ("Fix checkout flow", "fix-checkout-flow"),
        ("Add tests", "add-tests"),
    ],
)
def test_name_from_text_matches_the_client_heuristic(text: str, expected: str) -> None:
    assert worktree_name_from_text(text) == expected


def test_name_from_text_splits_a_word_that_cannot_fit() -> None:
    assert worktree_name_from_text("a" * 60) == "a" * MAX_WORKTREE_NAME_LENGTH


@pytest.mark.parametrize("text", ["", "   ", "!!!", "作業ツリー", "🙂"])
def test_name_from_text_is_empty_when_nothing_survives(text: str) -> None:
    assert worktree_name_from_text(text) == ""


def test_name_from_text_keeps_a_lone_stop_word() -> None:
    assert worktree_name_from_text("the") == "the"


# NFKD turns an accent into a combining mark, which then reads as a separator.
# Inherited verbatim from the TypeScript heuristic this replaces.
def test_name_from_text_splits_accented_words() -> None:
    assert worktree_name_from_text("Café résumé") == "cafe-re-sume"


@pytest.mark.parametrize("suffix", [2, 9, 10, 99, 100])
def test_name_with_suffix_stays_within_the_length_limit(suffix: int) -> None:
    name = worktree_name_with_suffix("a" * MAX_WORKTREE_NAME_LENGTH, suffix)

    assert len(name) <= MAX_WORKTREE_NAME_LENGTH
    assert name.endswith(f"-{suffix}")


def test_name_with_suffix_cuts_on_a_word_boundary() -> None:
    name = "work-something-random-to-demonstrate-the"

    assert len(name) == MAX_WORKTREE_NAME_LENGTH
    assert (
        worktree_name_with_suffix(name, 2) == "work-something-random-to-demonstrate-2"
    )


def test_name_with_suffix_leaves_a_short_name_intact() -> None:
    assert worktree_name_with_suffix("add-tests", 3) == "add-tests-3"
