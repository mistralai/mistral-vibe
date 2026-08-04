from __future__ import annotations

from dataclasses import dataclass

PREFIX_MULTIPLIER = 2.0
WORD_BOUNDARY_MULTIPLIER = 1.8
CONSECUTIVE_MULTIPLIER = 1.3


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    score: float
    matched_indices: tuple[int, ...]


def fuzzy_match(pattern: str, text: str, text_lower: str | None = None) -> MatchResult:
    if not pattern:
        return MatchResult(matched=True, score=0.0, matched_indices=())

    if text_lower is None:
        text_lower = text.lower()
    return _find_best_match(pattern, pattern.lower(), text_lower, text)


def _find_best_match(
    pattern_original: str, pattern_lower: str, text_lower: str, text_original: str
) -> MatchResult:
    if len(pattern_lower) > len(text_lower):
        return MatchResult(matched=False, score=0.0, matched_indices=())

    if text_lower.startswith(pattern_lower):
        indices = tuple(range(len(pattern_lower)))
        score = _calculate_score(
            pattern_original, pattern_lower, text_lower, indices, text_original
        )
        return MatchResult(
            matched=True, score=score * PREFIX_MULTIPLIER, matched_indices=indices
        )

    best_score = -1.0
    best_indices: tuple[int, ...] = ()

    for matcher in (
        _try_word_boundary_match,
        _try_consecutive_match,
        _try_subsequence_match,
    ):
        match = matcher(pattern_original, pattern_lower, text_lower, text_original)
        if match.matched and match.score > best_score:
            best_score = match.score
            best_indices = match.matched_indices

    if best_score >= 0:
        return MatchResult(matched=True, score=best_score, matched_indices=best_indices)

    return MatchResult(matched=False, score=0.0, matched_indices=())


def _try_word_boundary_match(
    pattern_original: str, pattern: str, text_lower: str, text_original: str
) -> MatchResult:
    indices: list[int] = []
    pattern_idx = 0

    for i, char in enumerate(text_lower):
        if pattern_idx >= len(pattern):
            break

        is_boundary = (
            i == 0
            or text_lower[i - 1] in "/-_."
            or (text_original[i].isupper() and not text_original[i - 1].isupper())
        )

        if char == pattern[pattern_idx]:
            if is_boundary or (indices and i == indices[-1] + 1) or not indices:
                indices.append(i)
                pattern_idx += 1

    if pattern_idx == len(pattern):
        score = _calculate_score(
            pattern_original, pattern, text_lower, tuple(indices), text_original
        )
        return MatchResult(
            matched=True,
            score=score * WORD_BOUNDARY_MULTIPLIER,
            matched_indices=tuple(indices),
        )

    return MatchResult(matched=False, score=0.0, matched_indices=())


def _try_consecutive_match(
    pattern_original: str, pattern: str, text_lower: str, text_original: str
) -> MatchResult:
    indices: list[int] = []
    pattern_idx = 0

    for i, char in enumerate(text_lower):
        if pattern_idx >= len(pattern):
            break

        if char == pattern[pattern_idx]:
            indices.append(i)
            pattern_idx += 1
        elif indices:
            indices.clear()
            pattern_idx = 0

    if pattern_idx == len(pattern):
        score = _calculate_score(
            pattern_original, pattern, text_lower, tuple(indices), text_original
        )
        return MatchResult(
            matched=True,
            score=score * CONSECUTIVE_MULTIPLIER,
            matched_indices=tuple(indices),
        )

    return MatchResult(matched=False, score=0.0, matched_indices=())


def _try_subsequence_match(
    pattern_original: str, pattern: str, text_lower: str, text_original: str
) -> MatchResult:
    indices: list[int] = []
    pattern_idx = 0

    for i, char in enumerate(text_lower):
        if pattern_idx >= len(pattern):
            break
        if char == pattern[pattern_idx]:
            indices.append(i)
            pattern_idx += 1

    if pattern_idx == len(pattern):
        score = _calculate_score(
            pattern_original, pattern, text_lower, tuple(indices), text_original
        )
        return MatchResult(matched=True, score=score, matched_indices=tuple(indices))

    return MatchResult(matched=False, score=0.0, matched_indices=())


def _calculate_score(
    pattern_original: str,
    pattern: str,
    text_lower: str,
    indices: tuple[int, ...],
    text_original: str,
) -> float:
    if not indices:
        return 0.0

    base_score = 100.0
    if indices[0] == 0:
        base_score += 50.0
    else:
        base_score -= indices[0] * 2

    consecutive_bonus = sum(
        10.0 for i in range(len(indices) - 1) if indices[i + 1] == indices[i] + 1
    )

    boundary_bonus = 0.0
    for idx in indices:
        if idx == 0 or text_lower[idx - 1] in "/-_.":
            boundary_bonus += 5.0
        elif text_original[idx].isupper() and (
            idx == 0 or not text_original[idx - 1].isupper()
        ):
            boundary_bonus += 3.0

    case_bonus = sum(
        2.0
        for i, text_idx in enumerate(indices)
        if i < len(pattern_original)
        and text_idx < len(text_original)
        and pattern_original[i] == text_original[text_idx]
    )

    gap_penalty = sum(
        (indices[i + 1] - indices[i] - 1) * 1.5 for i in range(len(indices) - 1)
    )

    return max(
        0.0, base_score + consecutive_bonus + boundary_bonus + case_bonus - gap_penalty
    )
