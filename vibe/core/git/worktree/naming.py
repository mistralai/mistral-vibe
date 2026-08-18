from __future__ import annotations

import re
import unicodedata

MAX_WORKTREE_NAME_LENGTH = 40

_MAX_WORKTREE_NAME_WORDS = 6
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
# Trailing filler reads as noise in a folder name. English-only: prompts in
# other locales just keep the word-boundary truncation.
_STOP_WORDS = frozenset({
    "a",
    "an",
    "and",
    "at",
    "for",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "the",
    "to",
    "with",
})


def worktree_name_from_text(text: str) -> str:
    words = _slugify(text).split("-")[:_MAX_WORKTREE_NAME_WORDS]
    return _trim("-".join(words))


def worktree_name_with_suffix(name: str, suffix: int) -> str:
    suffix_text = f"-{suffix}"
    return f"{_trim(name, reserved=len(suffix_text))}{suffix_text}"


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).lower()
    return _NON_SLUG_CHARS.sub("-", normalized).strip("-")


def _trim(value: str, *, reserved: int = 0) -> str:
    max_length = MAX_WORKTREE_NAME_LENGTH - reserved
    truncated = (
        value if len(value) <= max_length else _cut_on_word_boundary(value, max_length)
    )
    words = [word for word in truncated.split("-") if word]
    return "-".join(_without_trailing_stop_words(words))


def _cut_on_word_boundary(value: str, max_length: int) -> str:
    cut = value[:max_length]
    if value[max_length] == "-":
        return cut
    boundary = cut.rfind("-")
    return cut if boundary == -1 else cut[:boundary]


def _without_trailing_stop_words(words: list[str]) -> list[str]:
    end = len(words)
    while end > 1 and words[end - 1] in _STOP_WORDS:
        end -= 1
    return words[:end]
