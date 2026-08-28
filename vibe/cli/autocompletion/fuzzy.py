from __future__ import annotations

from dataclasses import dataclass

# Pure-Python fuzzy matcher inspired by fzf's Smith-Waterman DP variant.
# Reimplemented locally (rather than depending on fzf) to avoid a native
# binary dependency.  Uses a two-phase design: DP finds the optimal
# character alignment, then a separate ranking score determines sort order.

# --- DP alignment scoring (determines optimal match positions) ---
#
# The DP uses a simplified per-position score to find the alignment that
# maximises overall quality.  The final ranking score is computed separately
# by ``_calculate_score`` so that it stays comparable across different pattern
# lengths (the DP score is only meaningful relative to other alignments of
# the *same* pattern).

_DP_MATCH = 1.0
_DP_GAP_START = 3.0
_DP_GAP_EXT = 1.0
_DP_BOUNDARY = 1.0
_DP_CAMEL = 0.8
_DP_CASE = 0.2
_DP_POSITION = 0.05

# --- Final ranking score (determines sort order across entries) ---

_SCORE_BASE = 100.0
_SCORE_PREFIX_BONUS = 50.0
_SCORE_POSITION_PENALTY = 2.0
_SCORE_CONSECUTIVE_BONUS = 10.0
_SCORE_BOUNDARY_BONUS = 5.0
_SCORE_CAMEL_BONUS = 3.0
_SCORE_CASE_BONUS = 2.0
_SCORE_GAP_PENALTY = 1.5

_WORD_BOUNDARY_CHARS = frozenset("/-_.")


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
    pattern_lower = pattern.lower()

    if len(pattern_lower) > len(text_lower):
        return MatchResult(matched=False, score=0.0, matched_indices=())

    indices = _find_optimal_match(pattern, pattern_lower, text_lower, text)
    if not indices:
        return MatchResult(matched=False, score=0.0, matched_indices=())

    score = _calculate_score(pattern, text_lower, text, indices)
    return MatchResult(matched=True, score=score, matched_indices=indices)


def _is_boundary(text_lower: str, text_orig: str, j: int) -> float:
    """DP boundary bonus for matching at text position *j*."""
    if j == 0:
        return _DP_BOUNDARY
    if text_lower[j - 1] in _WORD_BOUNDARY_CHARS:
        return _DP_BOUNDARY
    if text_orig[j].isupper() and not text_orig[j - 1].isupper():
        return _DP_CAMEL
    return 0.0


class _GapTracker:
    """Running maximum for the gap option in the DP, updated in O(1) per text position."""

    __slots__ = ("best", "best_k")

    def __init__(self) -> None:
        self.best: float = float("-inf")
        self.best_k: int = -1

    def update(self, prev_dp: list[float], i: int, j: int) -> None:
        """Extend the existing gap, then try starting a new gap from position j-2."""
        if self.best > float("-inf"):
            self.best -= _DP_GAP_EXT
        k = j - 2
        if k >= i - 1 and prev_dp[k] > float("-inf"):
            new_gap = prev_dp[k] - _DP_GAP_START
            if new_gap > self.best:
                self.best = new_gap
                self.best_k = k


def _find_optimal_match(
    pattern_orig: str, pattern_lower: str, text_lower: str, text_orig: str
) -> tuple[int, ...]:
    """Find the highest-scoring alignment of *pattern_lower* in *text_lower*.

    Uses an O(m*n) dynamic-programming pass inspired by fzf's Smith-Waterman
    variant.  For each pattern character *i* and text position *j* the DP tracks
    the best score achievable, using a running gap-maximum so that each cell is
    computed in O(1).  Backtracking reconstructs the matched indices.
    """
    m = len(pattern_lower)
    n = len(text_lower)
    NEG_INF = float("-inf")

    # dp[i][j] = best DP score for pattern[0..i] ending at text position j
    dp: list[list[float]] = [[NEG_INF] * n for _ in range(m)]
    # bt[i][j] = text position where pattern[i-1] was matched
    bt: list[list[int]] = [[-1] * n for _ in range(m)]

    for i in range(m):
        gap = _GapTracker()

        # Pattern char i needs at least (m - 1 - i) chars after it, so j
        # can go up to n - (m - 1 - i) - 1, i.e. range(..., n - (m - 1 - i)).
        for j in range(i, n - (m - 1 - i)):
            # Update the running gap maximum *before* testing j so that it
            # reflects gaps ending strictly before j (i.e. k < j - 1).
            if i > 0 and j >= i + 1:
                gap.update(dp[i - 1], i, j)

            if text_lower[j] != pattern_lower[i]:
                continue

            boundary = _is_boundary(text_lower, text_orig, j)
            case = (
                _DP_CASE
                if i < len(pattern_orig)
                and j < len(text_orig)
                and pattern_orig[i] == text_orig[j]
                else 0.0
            )
            char_score = _DP_MATCH + boundary + case

            if i == 0:
                dp[i][j] = char_score - j * _DP_POSITION
            else:
                consecutive = dp[i - 1][j - 1] if j >= 1 else NEG_INF
                if consecutive >= gap.best and consecutive > NEG_INF:
                    dp[i][j] = consecutive + char_score
                    bt[i][j] = j - 1
                elif gap.best > NEG_INF:
                    dp[i][j] = gap.best + char_score
                    bt[i][j] = gap.best_k

    # Find the best ending position.
    best_end = max(range(n), key=lambda j: dp[m - 1][j], default=-1)
    if dp[m - 1][best_end] == NEG_INF:
        return ()

    # Backtrack to reconstruct matched indices.
    indices: list[int] = []
    j = best_end
    for i in range(m - 1, -1, -1):
        indices.append(j)
        j = bt[i][j]
    indices.reverse()

    return tuple(indices)


def _calculate_score(
    pattern_orig: str, text_lower: str, text_orig: str, indices: tuple[int, ...]
) -> float:
    """Compute the final ranking score from the optimal matched indices.

    This is intentionally independent of pattern length so that scores remain
    comparable across queries of different lengths (matching the behaviour of
    the previous greedy algorithm).
    """
    if not indices:
        return 0.0

    base = _SCORE_BASE
    if indices[0] == 0:
        base += _SCORE_PREFIX_BONUS
    else:
        base -= indices[0] * _SCORE_POSITION_PENALTY

    consecutive = sum(
        _SCORE_CONSECUTIVE_BONUS
        for i in range(len(indices) - 1)
        if indices[i + 1] == indices[i] + 1
    )

    boundary = 0.0
    for idx in indices:
        if idx == 0 or text_lower[idx - 1] in _WORD_BOUNDARY_CHARS:
            boundary += _SCORE_BOUNDARY_BONUS
        elif text_orig[idx].isupper() and (
            idx == 0 or not text_orig[idx - 1].isupper()
        ):
            boundary += _SCORE_CAMEL_BONUS

    case = sum(
        _SCORE_CASE_BONUS
        for i, text_idx in enumerate(indices)
        if i < len(pattern_orig)
        and text_idx < len(text_orig)
        and pattern_orig[i] == text_orig[text_idx]
    )

    gap = sum(
        (indices[i + 1] - indices[i] - 1) * _SCORE_GAP_PENALTY
        for i in range(len(indices) - 1)
    )

    return max(0.0, base + consecutive + boundary + case - gap)
