from __future__ import annotations

from pathlib import Path

from vibe.core.types import IMAGE_EXTENSIONS

_QUOTES: frozenset[str] = frozenset({"'", '"'})
_PATH_ROOTS: frozenset[str] = frozenset({"/", "~"})
_TOKEN_BOUNDARY_CHARS: frozenset[str] = frozenset("(<[")
_MIN_QUOTED_LEN = 2


def maybe_prepend_at_for_image_path(pasted: str) -> str:
    text = pasted.strip()
    if not text or "\n" in text or "\r" in text:
        return pasted
    candidate = _unescape_spaces(_strip_matched_quotes(text))
    if not candidate or candidate[0] not in _PATH_ROOTS:
        return pasted
    if not _is_image_file(candidate):
        return pasted
    return f"@{_quote_if_needed(candidate)}"


def rewrite_bare_image_paths_in_text(text: str) -> str:
    """Scan `text` for bare absolute image paths (raw, backslash-escaped, or
    quoted) and prepend `@` to each. Idempotent: tokens already preceded by
    `@` are not touched. Used as a text-changed hook to recover the UX of
    drag-and-drop in terminals that do not emit bracketed-paste sequences.
    """
    # Per-keystroke fast path: a bare path token must start with `/`, `~`, or
    # a quote, so skip the per-token stat() walk if none are present.
    if not any(ch in text for ch in "/~'\""):
        return text
    out: list[str] = []
    pos = 0
    while pos < len(text):
        if _at_token_boundary(text, pos):
            token, end = _extract_path_token(text, pos)
            if token is not None and _is_image_file(token):
                out.append(f"@{_quote_if_needed(token)}")
                pos = end
                continue
        out.append(text[pos])
        pos += 1
    return "".join(out)


def _is_image_file(candidate: str) -> bool:
    try:
        resolved = Path(candidate).expanduser()
    except RuntimeError:
        # `~unknownuser/...` raises when the user cannot be resolved.
        return False
    return (
        resolved.is_absolute()
        and resolved.suffix.lower() in IMAGE_EXTENSIONS
        and resolved.is_file()
    )


def _quote_if_needed(path: str) -> str:
    return f"'{path}'" if " " in path else path


def _unescape_spaces(text: str) -> str:
    return text.replace("\\ ", " ")


def _strip_matched_quotes(text: str) -> str:
    if len(text) >= _MIN_QUOTED_LEN and text[0] in _QUOTES and text[-1] == text[0]:
        return text[1:-1]
    return text


def _at_token_boundary(text: str, pos: int) -> bool:
    if pos == 0:
        return True
    prev = text[pos - 1]
    if prev == "@":
        return False
    return prev.isspace() or prev in _TOKEN_BOUNDARY_CHARS


def _extract_path_token(text: str, pos: int) -> tuple[str | None, int]:
    head = text[pos]
    if head in _QUOTES:
        return _extract_quoted(text, pos, quote=head)
    if head in _PATH_ROOTS:
        return _extract_bare(text, pos)
    return None, pos


def _extract_quoted(text: str, start: int, *, quote: str) -> tuple[str | None, int]:
    end = text.find(quote, start + 1)
    if end == -1:
        return None, start
    return text[start + 1 : end], end + 1


def _extract_bare(text: str, start: int) -> tuple[str | None, int]:
    out: list[str] = []
    end = start
    n = len(text)
    while end < n:
        ch = text[end]
        if ch == "\\" and end + 1 < n and text[end + 1] == " ":
            out.append(" ")
            end += 2
            continue
        if ch.isspace():
            break
        out.append(ch)
        end += 1
    if end == start:
        return None, start
    return "".join(out), end
