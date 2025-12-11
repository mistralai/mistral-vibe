from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from pathlib import Path

DEFAULT_IGNORE_PATTERNS: list[tuple[str, bool]] = [
    (".git/", True),
    ("__pycache__/", True),
    ("node_modules/", True),
    (".DS_Store", True),
    ("*.pyc", True),
    ("*.log", True),
    (".vscode/", True),
    (".idea/", True),
    ("/build/", True),
    ("dist/", True),
    ("target/", True),
    (".next/", True),
    (".nuxt/", True),
    ("coverage/", True),
    (".nyc_output/", True),
    ("*.egg-info", True),
    (".pytest_cache/", True),
    (".tox/", True),
    ("vendor/", True),
    ("third_party/", True),
    ("deps/", True),
    ("*.min.js", True),
    ("*.min.css", True),
    ("*.bundle.js", True),
    ("*.chunk.js", True),
    (".cache/", True),
    ("tmp/", True),
    ("temp/", True),
    ("logs/", True),
    (".uv-cache/", True),
    (".ruff_cache/", True),
    (".venv/", True),
    ("venv/", True),
    (".mypy_cache/", True),
    ("htmlcov/", True),
    (".coverage", True),
]


@dataclass(slots=True)
class CompiledPattern:
    raw: str
    stripped: str
    is_exclude: bool
    dir_only: bool
    name_only: bool
    anchor_root: bool


class IgnoreRules:
    def __init__(self, defaults: list[tuple[str, bool]] | None = None) -> None:
        self._defaults = defaults or DEFAULT_IGNORE_PATTERNS
        self._patterns: list[CompiledPattern] | None = None
        self._root: Path | None = None

    def ensure_for_root(self, root: Path) -> None:
        resolved_root = root.resolve()
        if self._patterns is None or self._root != resolved_root:
            self._patterns = self._build_patterns(resolved_root)
            self._root = resolved_root

    def should_ignore(self, rel_str: str, name: str, is_dir: bool) -> bool:
        if not self._patterns:
            return False

        ignored = False
        for pattern in self._patterns:
            if self._matches(rel_str, name, is_dir, pattern):
                ignored = pattern.is_exclude
        return ignored

    def reset(self) -> None:
        self._patterns = None
        self._root = None

    def _build_patterns(self, root: Path) -> list[CompiledPattern]:
        patterns: list[CompiledPattern] = []
        for raw, is_exclude in self._defaults:
            anchor_root = raw.startswith("/")
            if anchor_root:
                raw = raw[1:]

            stripped = raw.rstrip("/")
            patterns.append(
                CompiledPattern(
                    raw=raw,
                    stripped=stripped,
                    is_exclude=is_exclude,
                    dir_only=raw.endswith("/"),
                    name_only="/" not in stripped,
                    anchor_root=anchor_root,
                )
            )

        gitignore_path = root / ".gitignore"
        if gitignore_path.exists():
            try:
                text = gitignore_path.read_text(encoding="utf-8")
            except Exception:
                return patterns

            for line in text.splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue

                if "#" in raw:
                    raw = raw.split("#", 1)[0].rstrip()
                    if not raw:
                        continue

                is_exclude = not raw.startswith("!")
                if not is_exclude:
                    raw = raw[1:].lstrip()
                    if not raw:
                        continue

                anchor_root = raw.startswith("/")
                if anchor_root:
                    raw = raw[1:]

                stripped = raw.rstrip("/")
                patterns.append(
                    CompiledPattern(
                        raw=raw,
                        stripped=stripped,
                        is_exclude=is_exclude,
                        dir_only=raw.endswith("/"),
                        name_only="/" not in stripped,
                        anchor_root=anchor_root,
                    )
                )

        return patterns

    def _matches(
        self, rel_str: str, name: str, is_dir: bool, pattern: CompiledPattern
    ) -> bool:
        if pattern.name_only:
            if pattern.anchor_root and "/" in rel_str:
                return False
            target = name
        else:
            target = rel_str

        if not fnmatch.fnmatch(target, pattern.stripped):
            return False

        return not pattern.dir_only or is_dir
