from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from vibe.core.autocompletion.file_indexer import FileIndexer, IndexEntry
from vibe.core.autocompletion.file_indexer.store import (
    ASCII_CODEPOINT_LIMIT,
    build_ascii_mask,
)
from vibe.core.autocompletion.fuzzy import fuzzy_match

DEFAULT_MAX_ENTRIES_TO_PROCESS = 32000
DEFAULT_TARGET_MATCHES = 100


class Completer:
    def get_completions(self, text: str, cursor_pos: int) -> list[str]:
        return []

    def get_completion_items(self, text: str, cursor_pos: int) -> list[tuple[str, str]]:
        return [
            (completion, "") for completion in self.get_completions(text, cursor_pos)
        ]

    def get_replacement_range(
        self, text: str, cursor_pos: int
    ) -> tuple[int, int] | None:
        return None


class CommandCompleter(Completer):
    def __init__(self, entries: Callable[[], list[tuple[str, str]]]) -> None:
        self._get_entries = entries

    def _build_lookup(self) -> tuple[list[str], dict[str, str]]:
        descriptions: dict[str, str] = {}
        for alias, description in self._get_entries():
            descriptions[alias] = description
        return list(descriptions.keys()), descriptions

    def get_completions(self, text: str, cursor_pos: int) -> list[str]:
        if not text.startswith("/"):
            return []

        aliases, _ = self._build_lookup()
        word = text[1:cursor_pos].lower()
        search_str = "/" + word
        return [alias for alias in aliases if alias.lower().startswith(search_str)]

    def get_completion_items(self, text: str, cursor_pos: int) -> list[tuple[str, str]]:
        if not text.startswith("/"):
            return []

        aliases, descriptions = self._build_lookup()
        word = text[1:cursor_pos].lower()
        search_str = "/" + word
        return [
            (alias, descriptions.get(alias, ""))
            for alias in aliases
            if alias.lower().startswith(search_str)
        ]

    def get_replacement_range(
        self, text: str, cursor_pos: int
    ) -> tuple[int, int] | None:
        if text.startswith("/"):
            return (0, cursor_pos)
        return None


class PathCompleter(Completer):
    class MatchRank(NamedTuple):
        exact_directory: int
        immediate_child_of_exact_path: int
        exact_filename: int
        preferred_stem_match: int
        exact_stem: int
        stem_prefix: int
        name_prefix: int
        extension_match: int
        fuzzy_score: float
        shallow_path: int

    def __init__(
        self,
        max_entries_to_process: int = DEFAULT_MAX_ENTRIES_TO_PROCESS,
        target_matches: int = DEFAULT_TARGET_MATCHES,
        watcher_enabled_getter: Callable[[], bool] | None = None,
    ) -> None:
        self._indexer = FileIndexer(should_enable_watcher=watcher_enabled_getter)
        self._max_entries_to_process = max_entries_to_process
        self._target_matches = target_matches

    class _SearchContext(NamedTuple):
        suffix: str
        search_pattern: str
        path_prefix: str
        immediate_only: bool
        search_pattern_ascii_mask: int | None

    def _extract_partial(self, before_cursor: str) -> str | None:
        if "@" not in before_cursor:
            return None

        at_index = before_cursor.rfind("@")
        fragment = before_cursor[at_index + 1 :]

        if " " in fragment:
            return None

        return fragment

    def _build_search_context(self, partial_path: str) -> _SearchContext:
        suffix = partial_path.split("/")[-1]
        search_pattern_ascii_mask = self._build_query_ascii_mask(partial_path)

        if not partial_path:
            # "@" => show top-level dir and files
            return self._SearchContext(
                search_pattern="",
                path_prefix="",
                suffix=suffix,
                immediate_only=True,
                search_pattern_ascii_mask=search_pattern_ascii_mask,
            )

        if partial_path.endswith("/"):
            # "@something/" => list immediate children
            return self._SearchContext(
                search_pattern="",
                path_prefix=partial_path,
                suffix=suffix,
                immediate_only=True,
                search_pattern_ascii_mask=search_pattern_ascii_mask,
            )

        return self._SearchContext(
            # => run fuzzy search across the index
            search_pattern=partial_path,
            path_prefix="",
            suffix=suffix,
            immediate_only=False,
            search_pattern_ascii_mask=search_pattern_ascii_mask,
        )

    def _build_query_ascii_mask(self, pattern: str) -> int | None:
        if any(ord(char) >= ASCII_CODEPOINT_LIMIT for char in pattern):
            return None
        return build_ascii_mask(pattern.lower())

    def _is_immediate_child_of_prefix(self, path_str: str, prefix: str) -> bool:
        prefix_without_slash = prefix.rstrip("/")
        prefix_with_slash = f"{prefix_without_slash}/"

        if path_str.startswith(prefix_with_slash):
            after_prefix = path_str[len(prefix_with_slash) :]
        else:
            idx = path_str.find(prefix_with_slash)
            if idx == -1 or (idx > 0 and path_str[idx - 1] != "/"):
                return False
            after_prefix = path_str[idx + len(prefix_with_slash) :]

        return bool(after_prefix) and "/" not in after_prefix

    def _matches_prefix(self, entry: IndexEntry, context: _SearchContext) -> bool:
        path_str = entry.rel

        if context.path_prefix:
            prefix_without_slash = context.path_prefix.rstrip("/")

            if path_str == prefix_without_slash and entry.is_dir:
                # do not suggest the dir itself (e.g. "@src/" => don't suggest "@src/")
                return False

            # only suggest files/dirs that are immediate children of the prefix
            return self._is_immediate_child_of_prefix(path_str, context.path_prefix)

        if context.immediate_only and "/" in path_str:
            # when user just typed "@", only show top-level entries
            return False

        # entry matches the prefix: let the fuzzy matcher decide if it's a good match
        return True

    def _is_visible(self, entry: IndexEntry, context: _SearchContext) -> bool:
        return not (entry.name.startswith(".") and not context.suffix.startswith("."))

    def _can_possibly_fuzzy_match(
        self, entry: IndexEntry, context: _SearchContext
    ) -> bool:
        if context.search_pattern_ascii_mask is None:
            return True
        return (
            entry.ascii_mask & context.search_pattern_ascii_mask
        ) == context.search_pattern_ascii_mask

    def _format_label(self, entry: IndexEntry) -> str:
        suffix = "/" if entry.is_dir else ""
        return f"@{entry.rel}{suffix}"

    def _build_match_rank(
        self, entry: IndexEntry, context: _SearchContext, fuzzy_score: float
    ) -> MatchRank:
        query = context.suffix.lower()
        if not query:
            return self.MatchRank(
                exact_directory=0,
                immediate_child_of_exact_path=0,
                exact_filename=0,
                preferred_stem_match=0,
                exact_stem=0,
                stem_prefix=0,
                name_prefix=0,
                extension_match=0,
                fuzzy_score=fuzzy_score,
                shallow_path=-entry.rel.count("/"),
            )

        name = entry.name.lower()
        rel = entry.rel.lower()
        stem = Path(entry.name).stem.lower()
        extension = Path(entry.name).suffix.lower()
        query_extension = Path(query).suffix.lower()
        query_stem = Path(query).stem.lower()
        query_looks_like_filename = "." in query
        query_looks_like_path = "/" in context.search_pattern
        exact_directory = int(entry.is_dir and rel == context.search_pattern.lower())
        immediate_child_of_exact_path = int(
            query_looks_like_path
            and self._is_immediate_child_of_prefix(rel, context.search_pattern.lower())
        )

        return self.MatchRank(
            exact_directory=exact_directory,
            immediate_child_of_exact_path=immediate_child_of_exact_path,
            exact_filename=int(query_looks_like_filename and name == query),
            preferred_stem_match=int(stem == query and extension != ".lock"),
            exact_stem=int(
                stem == query or (query_looks_like_filename and stem == query_stem)
            ),
            stem_prefix=int(
                stem.startswith(query_stem if query_looks_like_filename else query)
            ),
            name_prefix=int(name.startswith(query)),
            extension_match=int(bool(query_extension) and extension == query_extension),
            fuzzy_score=fuzzy_score,
            shallow_path=-entry.rel.count("/"),
        )

    def _score_matches(
        self, entries: list[IndexEntry], context: _SearchContext
    ) -> list[tuple[str, PathCompleter.MatchRank]]:
        scored_matches: list[tuple[str, PathCompleter.MatchRank]] = []

        for i, entry in enumerate(entries):
            if i >= self._max_entries_to_process:
                break

            if not self._matches_prefix(entry, context):
                continue

            if not self._is_visible(entry, context):
                continue

            label = self._format_label(entry)

            if not context.search_pattern:
                rank = self._build_match_rank(entry, context, 0.0)
                scored_matches.append((label, rank))
                if len(scored_matches) >= self._target_matches:
                    break
                continue

            if not self._can_possibly_fuzzy_match(entry, context):
                continue

            match_result = fuzzy_match(
                context.search_pattern, entry.rel, entry.rel_lower
            )
            if match_result.matched:
                rank = self._build_match_rank(entry, context, match_result.score)
                scored_matches.append((label, rank))

        # Sort alphabetically first, then by descending rank; Python's stable sort
        # keeps the label order for entries with equal ranks.
        scored_matches.sort(key=lambda x: x[0])
        scored_matches.sort(key=lambda x: x[1], reverse=True)
        return scored_matches[: self._target_matches]

    def _collect_matches(self, text: str, cursor_pos: int) -> list[str]:
        before_cursor = text[:cursor_pos]
        partial_path = self._extract_partial(before_cursor)
        if partial_path is None:
            return []

        context = self._build_search_context(partial_path)

        try:
            # TODO (Vince): doing the assumption that "." is the root directory... Reliable?
            file_index = self._indexer.get_index(Path("."))
        except (OSError, RuntimeError):
            return []

        scored_matches = self._score_matches(file_index, context)
        return [path for path, _ in scored_matches]

    def get_completions(self, text: str, cursor_pos: int) -> list[str]:
        return self._collect_matches(text, cursor_pos)

    def get_completion_items(self, text: str, cursor_pos: int) -> list[tuple[str, str]]:
        matches = self._collect_matches(text, cursor_pos)
        return [(completion, "") for completion in matches]

    def get_replacement_range(
        self, text: str, cursor_pos: int
    ) -> tuple[int, int] | None:
        before_cursor = text[:cursor_pos]
        if "@" in before_cursor:
            at_index = before_cursor.rfind("@")
            return (at_index, cursor_pos)
        return None


class MultiCompleter(Completer):
    def __init__(self, completers: list[Completer]) -> None:
        self.completers = completers

    def get_completions(self, text: str, cursor_pos: int) -> list[str]:
        all_completions = []
        for completer in self.completers:
            completions = completer.get_completions(text, cursor_pos)
            all_completions.extend(completions)

        seen = set()
        unique = []
        for comp in all_completions:
            if comp not in seen:
                seen.add(comp)
                unique.append(comp)

        return unique

    def get_replacement_range(
        self, text: str, cursor_pos: int
    ) -> tuple[int, int] | None:
        for completer in self.completers:
            range_result = completer.get_replacement_range(text, cursor_pos)
            if range_result is not None:
                return range_result
        return None
