from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Callable

from grep_ast import TreeContext

# Try to import tiktoken for accurate token counting
_tiktoken_encoder = None
try:
    import tiktoken
    _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
except ImportError:
    pass


def get_default_token_counter() -> Callable[[str], int]:
    """Get the best available token counter.

    Returns tiktoken-based counter if available, otherwise character-based estimate.
    """
    if _tiktoken_encoder is not None:
        return lambda text: len(_tiktoken_encoder.encode(text, disallowed_special=()))
    return lambda text: int(len(text) / 4.0)


def _select_ranked_tags(
    ranked_tags: list[tuple[tuple[str, str], float]],
    definitions: dict[tuple[str, str], set[Any]],
    map_tokens: int,
    chat_rel_fnames: set[str],
    token_counter_func: Callable[[str], int] | None = None,
) -> list[tuple[tuple[str, str], float]]:
    if not ranked_tags:
        return []

    count_tokens = token_counter_func or get_default_token_counter()
    num_tags = len(ranked_tags)
    lower = 0
    upper = num_tags
    best_count = 0
    middle = min(map_tokens // 25, num_tags)

    while lower <= upper:
        middle = max(0, min(middle, num_tags))
        current_tags = ranked_tags[:middle]
        tree_text = to_tree(current_tags, definitions, chat_rel_fnames)
        tokens = count_tokens(tree_text)

        if tokens <= map_tokens:
            best_count = middle
            lower = middle + 1
        else:
            upper = middle - 1

        middle = (lower + upper) // 2

        if lower > upper:
            break

    return ranked_tags[:best_count]


def render_repo_map(
    ranked_tags: list[tuple[tuple[str, str], float]],
    definitions: dict[tuple[str, str], set[Any]],
    map_tokens: int,
    chat_rel_fnames: set[str],
    token_counter_func: Callable[[str], int] | None = None,
) -> str:
    """Select top ranked tags and render them into a token-limited repository map."""
    selected_tags = _select_ranked_tags(
        ranked_tags,
        definitions,
        map_tokens,
        chat_rel_fnames,
        token_counter_func,
    )
    if not selected_tags:
        return ""

    return to_tree(selected_tags, definitions, chat_rel_fnames)


def _collect_lines_of_interest(
    ranked_tags: list[tuple[tuple[str, str], float]],
    definitions: dict[tuple[str, str], set[Any]],
    chat_rel_fnames: set[str],
) -> dict[str, tuple[set[int], str]]:
    files_to_render: dict[str, tuple[set[int], str]] = {}

    for (fname, ident), _ in ranked_tags:
        if fname in chat_rel_fnames:
            continue

        tags = definitions.get((fname, ident))
        if not tags:
            continue

        if fname not in files_to_render:
            first_tag = next(iter(tags))
            files_to_render[fname] = (set(), first_tag.rel_fname)

        lines, _ = files_to_render[fname]
        for tag in tags:
            lines.add(tag.line)

    return files_to_render


def to_tree(
    ranked_tags: list[tuple[tuple[str, str], float]],
    definitions: dict[tuple[str, str], set[Any]],
    chat_rel_fnames: set[str],
) -> str:
    """Render the tags into a tree-like structure using grep_ast."""
    if not ranked_tags:
        return ""

    files_to_render = _collect_lines_of_interest(
        ranked_tags,
        definitions,
        chat_rel_fnames,
    )
    output = []

    for fname in sorted(files_to_render.keys()):
        lines, display_name = files_to_render[fname]
        if not lines:
            continue

        try:
            code = Path(fname).read_text(encoding="utf-8", errors="ignore")
            context = TreeContext(
                display_name,
                code,
                color=False,
                line_number=False,
                child_context=False,
                last_line=False,
                margin=0,
                mark_lois=False,
                loi_pad=0,
                show_top_of_file_parent_scope=False,
            )
            context.lines_of_interest = set(lines)
            context.add_context()
            output.append(context.format())
        except Exception:
            pass

    return "\n".join(output)


def _collect_symbols(
    ranked_tags: list[tuple[tuple[str, str], float]],
    definitions: dict[tuple[str, str], set[Any]],
    chat_rel_fnames: set[str],
) -> list[tuple[str, list[str]]]:
    files_to_symbols: dict[str, tuple[str, set[str]]] = {}

    for (fname, ident), _ in ranked_tags:
        if fname in chat_rel_fnames:
            continue

        tags = definitions.get((fname, ident))
        if not tags:
            continue

        if fname not in files_to_symbols:
            first_tag = next(iter(tags))
            files_to_symbols[fname] = (first_tag.rel_fname, set())

        _, symbols = files_to_symbols[fname]
        symbols.add(ident)

    output: list[tuple[str, list[str]]] = []
    for fname in sorted(files_to_symbols.keys()):
        display_name, symbols = files_to_symbols[fname]
        output.append((display_name, sorted(symbols)))

    return output


def render_repo_map_markdown(
    ranked_tags: list[tuple[tuple[str, str], float]],
    definitions: dict[tuple[str, str], set[Any]],
    map_tokens: int,
    chat_rel_fnames: set[str],
    token_counter_func: Callable[[str], int] | None = None,
    include_all: bool = False,
) -> str:
    selected_tags = ranked_tags if include_all else _select_ranked_tags(
        ranked_tags,
        definitions,
        map_tokens,
        chat_rel_fnames,
        token_counter_func,
    )
    if not selected_tags:
        return ""

    files_with_symbols = _collect_symbols(
        selected_tags,
        definitions,
        chat_rel_fnames,
    )
    if not files_with_symbols:
        return ""

    lines = ["# RepoMap", ""]
    for display_name, symbols in files_with_symbols:
        lines.append(f"- {display_name}")
        for symbol in symbols:
            lines.append(f"  - {symbol}")

    return "\n".join(lines)
