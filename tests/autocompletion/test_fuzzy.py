from __future__ import annotations

from vibe.core.autocompletion.fuzzy import fuzzy_match


def test_empty_pattern_matches_anything() -> None:
    result = fuzzy_match("", "any_text")

    assert result.matched is True
    assert result.score == 0.0
    assert result.matched_indices == ()


def test_matches_exact_prefix() -> None:
    result = fuzzy_match("src/", "src/main.py")

    assert result.matched_indices == (0, 1, 2, 3)


def test_no_match_when_characters_are_out_of_order() -> None:
    result = fuzzy_match("ms", "src/main.py")

    assert result.matched is False


def test_treats_consecutive_characters_as_subsequence() -> None:
    result = fuzzy_match("main", "src/main.py")

    assert result.matched_indices == (4, 5, 6, 7)


def test_ignores_case() -> None:
    result = fuzzy_match("SRC", "src/main.py")

    assert result.matched_indices == (0, 1, 2)


def test_treats_scattered_characters_as_subsequence() -> None:
    result = fuzzy_match("sm", "src/main.py")

    assert result.matched_indices == (0, 4)


def test_treats_path_separator_as_word_boundary() -> None:
    result = fuzzy_match("m", "src/main.py")

    assert result.matched_indices == (4,)


def test_prefers_word_boundary_matching_over_subsequence() -> None:
    boundary_result = fuzzy_match("ma", "src/main.py")
    subsequence_result = fuzzy_match("ma", "src/important.py")

    assert boundary_result.score > subsequence_result.score


def test_scores_exact_prefix_match_higher_than_consecutive_and_subsequence() -> None:
    prefix_result = fuzzy_match("src", "src/main.py")
    consecutive_result = fuzzy_match("main", "src/main.py")
    subsequence_result = fuzzy_match("sm", "src/main.py")

    assert prefix_result.matched_indices == (0, 1, 2)
    assert prefix_result.score > consecutive_result.score
    assert prefix_result.score > subsequence_result.score


def test_finds_no_match_when_pattern_is_longer_than_entry() -> None:
    result = fuzzy_match("very_long_pattern", "short")

    assert result.matched is False


def test_prefers_consecutive_match_over_subsequence() -> None:
    consecutive = fuzzy_match("main", "src/main.py")
    subsequence = fuzzy_match("mn", "src/main.py")

    assert consecutive.score > subsequence.score


def test_prefers_case_sensitive_match_over_case_insensitive() -> None:
    case_match = fuzzy_match("Main", "src/Main.py")
    case_insensitive_match = fuzzy_match("main", "src/Main.py")

    assert case_match.score > case_insensitive_match.score


def test_treats_uppercase_letter_as_word_boundary() -> None:
    result = fuzzy_match("MP", "src/MainPy.py")

    assert result.matched_indices == (4, 8)


def test_favors_earlier_positions() -> None:
    result = fuzzy_match("a", "banana")

    assert result.matched_indices == (1,)
