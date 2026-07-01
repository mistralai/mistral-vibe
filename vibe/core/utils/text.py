from __future__ import annotations


def line_contexts(content: str, snippet: str) -> list[tuple[int, str, str]]:
    """``(start_line, prefix, suffix)`` per match, completing it to whole lines."""
    if not snippet.strip("\n"):
        return []
    # Anchor at the match position so the whole-line expansion (prefix + snippet)
    # covers every line the edit touches, including the line a leading-newline
    # snippet starts modifying. start_line is the file line of that first row, so
    # the diff gutter offset stays correct.
    results: list[tuple[int, str, str]] = []
    pos = content.find(snippet)
    while pos != -1:
        start_line = content.count("\n", 0, pos) + 1
        line_start = content.rfind("\n", 0, pos) + 1
        prefix = content[line_start:pos]
        match_end = pos + len(snippet)
        # A match ending on a line boundary has no partial trailing line.
        if match_end > 0 and content[match_end - 1] == "\n":
            suffix = ""
        else:
            line_end = content.find("\n", match_end)
            if line_end == -1:
                line_end = len(content)
            suffix = content[match_end:line_end]
        results.append((start_line, prefix, suffix))
        # Advance past the match (non-overlapping, mirroring str.replace).
        pos = content.find(snippet, match_end)
    return results
