from __future__ import annotations


def snippet_start_line(content: str, snippet: str) -> int | None:
    if not snippet.strip("\n"):
        return None
    if (pos := content.find(snippet)) == -1:
        return None
    # Skip leading newlines so the reported line is the first content line,
    # aligning the gutter with the diff (which renders the snippet stripped).
    leading = len(snippet) - len(snippet.lstrip("\n"))
    return content.count("\n", 0, pos + leading) + 1
