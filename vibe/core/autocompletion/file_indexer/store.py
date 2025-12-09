from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path

from vibe.core.autocompletion.file_indexer.ignore_rules import IgnoreRules
from vibe.core.autocompletion.file_indexer.watcher import Change


@dataclass(slots=True)
class FileIndexStats:
    rebuilds: int = 0
    incremental_updates: int = 0


@dataclass(slots=True)
class IndexEntry:
    rel: str
    rel_lower: str
    name: str
    path: Path
    is_dir: bool


class FileIndexStore:
    def __init__(
        self,
        ignore_rules: IgnoreRules,
        stats: FileIndexStats,
        mass_change_threshold: int = 200,
    ) -> None:
        self._ignore_rules = ignore_rules
        self._stats = stats
        self._mass_change_threshold = mass_change_threshold
        self._entries_by_rel: dict[str, IndexEntry] = {}
        self._ordered_entries: list[IndexEntry] | None = None
        self._root: Path | None = None

    @property
    def root(self) -> Path | None:
        return self._root

    def clear(self) -> None:
        self._entries_by_rel.clear()
        self._ordered_entries = None
        self._root = None

    def rebuild(
        self, root: Path, should_cancel: Callable[[], bool] | None = None
    ) -> None:
        resolved_root = root.resolve()
        self._ignore_rules.ensure_for_root(resolved_root)
        entries = self._walk_directory(resolved_root, cancel_check=should_cancel)
        self._entries_by_rel = {entry.rel: entry for entry in entries}
        self._ordered_entries = entries
        self._root = resolved_root
        self._stats.rebuilds += 1

    def snapshot(self) -> list[IndexEntry]:
        if not self._entries_by_rel:
            return []

        if self._ordered_entries is None:
            self._ordered_entries = sorted(
                self._entries_by_rel.values(), key=lambda entry: entry.rel
            )

        return list(self._ordered_entries)

    def apply_changes(self, changes: list[tuple[Change, Path]]) -> None:
        if self._root is None:
            return

        if len(changes) > self._mass_change_threshold:
            self.rebuild(self._root)
            return

        modified = False
        for change, path in changes:
            try:
                rel_str = path.relative_to(self._root).as_posix()
            except ValueError:
                continue

            if not rel_str:
                continue

            if change is Change.deleted:
                if self._remove_entry(rel_str):
                    modified = True
                continue

            if not path.exists():
                continue

            if path.is_dir():
                dir_entry = self._create_entry(rel_str, path.name, path, True)
                if dir_entry:
                    self._entries_by_rel[rel_str] = dir_entry
                    modified = True
                for entry in self._walk_directory(path, rel_str):
                    self._entries_by_rel[entry.rel] = entry
                    modified = True
            else:
                file_entry = self._create_entry(rel_str, path.name, path, False)
                if file_entry:
                    self._entries_by_rel[file_entry.rel] = file_entry
                    modified = True

        if modified:
            self._ordered_entries = None
            self._stats.incremental_updates += 1

    def _create_entry(
        self, rel_str: str, name: str, path: Path, is_dir: bool
    ) -> IndexEntry | None:
        if self._ignore_rules.should_ignore(rel_str, name, is_dir):
            return None
        return IndexEntry(
            rel=rel_str, rel_lower=rel_str.lower(), name=name, path=path, is_dir=is_dir
        )

    def _walk_directory(
        self,
        directory: Path,
        rel_prefix: str = "",
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[IndexEntry]:
        results: list[IndexEntry] = []
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if cancel_check and cancel_check():
                        break

                    is_dir = entry.is_dir(follow_symlinks=False)
                    name = entry.name
                    rel_str = f"{rel_prefix}/{name}" if rel_prefix else name
                    path = Path(entry.path)

                    index_entry = self._create_entry(rel_str, name, path, is_dir)
                    if not index_entry:
                        continue

                    results.append(index_entry)

                    if is_dir:
                        results.extend(
                            self._walk_directory(path, rel_str, cancel_check)
                        )
        except (PermissionError, OSError):
            pass

        return results

    def _remove_entry(self, rel_str: str) -> bool:
        entry = self._entries_by_rel.pop(rel_str, None)
        if not entry:
            return False

        if entry.is_dir:
            prefix = f"{rel_str}/"
            to_remove = [key for key in self._entries_by_rel if key.startswith(prefix)]
            for key in to_remove:
                self._entries_by_rel.pop(key, None)

        return True
