from __future__ import annotations


def snippet_start_line(content: str, snippet: str) -> int | None:
    lines = snippet_start_lines(content, snippet)
    return lines[0] if lines else None


def snippet_start_lines(content: str, snippet: str) -> list[int]:
    if not snippet.strip("\n"):
        return []
    # Skip leading newlines so the reported line is the first content line,
    # aligning the gutter with the diff (which renders the snippet stripped).
    leading = len(snippet) - len(snippet.lstrip("\n"))
    lines: list[int] = []
    pos = content.find(snippet)
    while pos != -1:
        lines.append(content.count("\n", 0, pos + leading) + 1)
        # Advance past the match (non-overlapping, mirroring str.replace).
        pos = content.find(snippet, pos + len(snippet))
    return lines
