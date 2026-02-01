from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, List, NamedTuple, Set, Optional, Tuple, Iterable, Any
import hashlib
import json
import os
import shutil
import threading
import time

import tree_sitter
from grep_ast import filename_to_lang
try:
    from grep_ast.tsl import get_language, get_parser
except ImportError:
    # Fallback or stub if grep_ast version differs
    def get_language(lang): raise NotImplementedError
    def get_parser(lang): raise NotImplementedError
from pygments.lexers import get_lexer_for_filename
from pygments.token import Name
from pygments import lex

Tag = namedtuple("Tag", "rel_fname fname line name kind parent".split(), defaults=(None,))

# Common variable names to exclude from Pygments backfill
_BACKFILL_STOPWORDS = frozenset({
    "i", "j", "k", "n", "m", "x", "y", "z", "a", "b", "c", "d", "e", "f",
    "id", "ok", "io", "os", "re", "db",
    "tmp", "err", "ctx", "req", "res", "ret", "val", "key", "buf", "msg", "arg",
    "args", "kwargs", "self", "cls", "this", "super",
    "true", "false", "null", "none", "nil",
    "int", "str", "bool", "float", "list", "dict", "set", "tuple",
    "len", "range", "print", "open", "type", "iter", "next", "map", "zip",
    "sum", "min", "max", "abs", "any", "all", "dir", "vars", "hash",
})


@dataclass
class ExtractionError:
    """Represents an error that occurred during tag extraction."""
    fname: str
    error_type: str
    message: str

    def __str__(self) -> str:
        return f"{self.error_type}: {self.fname}: {self.message}"


@dataclass
class ExtractionResult:
    """Result of tag extraction containing both tags and any errors encountered."""
    tags: list[Tag] = field(default_factory=list)
    errors: list[ExtractionError] = field(default_factory=list)
    files_processed: int = 0
    files_skipped: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    def merge(self, other: ExtractionResult) -> ExtractionResult:
        """Merge another ExtractionResult into this one."""
        return ExtractionResult(
            tags=self.tags + other.tags,
            errors=self.errors + other.errors,
            files_processed=self.files_processed + other.files_processed,
            files_skipped=self.files_skipped + other.files_skipped,
            cache_hits=self.cache_hits + other.cache_hits,
            cache_misses=self.cache_misses + other.cache_misses,
        )

class TagExtractor:
    """Extracts tags from source files using tree-sitter with caching support.

    Thread-safety: The cache metrics (_cache_hits, _cache_misses) are protected
    by a threading lock for accurate counting during parallel execution.
    The diskcache backend handles its own concurrency.
    """

    def __init__(
        self,
        cache_dir: str | None = None,
        known_definitions: set[str] | None = None,
        project_root: str | None = None,
    ):
        self.cache = None
        self._cache_hits = 0
        self._cache_misses = 0
        self._lock = threading.Lock()  # Protects metric counters
        # Known definitions for filtering Pygments backfill (optional)
        self._known_definitions = known_definitions or set()
        # Project root for relative path cache keys (avoids cache invalidation on folder moves)
        self._project_root = project_root
        if cache_dir:
            import diskcache
            # Add 500MB size limit with LRU eviction
            self.cache = diskcache.Cache(cache_dir, size_limit=500_000_000)

    def _get_cache_key(self, fname: str) -> str:
        """Generate a cache key using relative paths when possible.

        Uses relative paths hashed with project root to avoid cache invalidation
        when the project folder is moved or renamed.
        """
        if self._project_root:
            try:
                rel_path = os.path.relpath(fname, self._project_root)
                # Hash project root to create a namespace
                root_hash = hashlib.md5(self._project_root.encode()).hexdigest()[:8]
                return f"{root_hash}:{rel_path}"
            except ValueError:
                # On Windows, relpath fails across drives
                pass
        return fname

    def _increment_cache_hits(self) -> None:
        """Thread-safe increment of cache hit counter."""
        with self._lock:
            self._cache_hits += 1

    def _increment_cache_misses(self) -> None:
        """Thread-safe increment of cache miss counter."""
        with self._lock:
            self._cache_misses += 1

    def get_tags(self, fname: str, rel_fname: str) -> tuple[list[Tag], ExtractionError | None]:
        """Get tags for a file, using cache if available.

        Uses size+mtime as fast pre-check, with content hash for validation
        to handle git operations and edge cases where mtime is unreliable.

        Returns:
            Tuple of (tags, error). Error is None on success.
        """
        if not os.path.isfile(fname):
            return [], ExtractionError(
                fname=fname,
                error_type="FileNotFound",
                message="File does not exist",
            )

        try:
            stat = os.stat(fname)
            mtime = stat.st_mtime
            size = stat.st_size
        except OSError as e:
            return [], ExtractionError(
                fname=fname,
                error_type="StatError",
                message=str(e),
            )

        # Skip very large files (> 1MB) - likely generated or binary
        if size > 1_000_000:
            return [], ExtractionError(
                fname=fname,
                error_type="FileTooLarge",
                message=f"File size {size} exceeds 1MB limit",
            )

        if self.cache:
            key = self._get_cache_key(fname)
            if key in self.cache:
                cached = self.cache[key]
                # Fast path: if mtime and size match, trust cache
                if cached.get("mtime") == mtime and cached.get("size") == size:
                    self._increment_cache_hits()
                    return cached["data"], None
                # Slow path: compute hash and compare
                content_hash = self._compute_file_hash(fname)
                if content_hash and cached.get("hash") == content_hash:
                    # Content unchanged despite mtime change (e.g., git checkout)
                    # Update mtime in cache for next time
                    cached["mtime"] = mtime
                    cached["size"] = size
                    self.cache[key] = cached
                    self._increment_cache_hits()
                    return cached["data"], None

        self._increment_cache_misses()

        # Cache miss - extract tags
        tags, error = self._get_tags_raw(fname, rel_fname)

        if self.cache and not error:
            key = self._get_cache_key(fname)
            content_hash = self._compute_file_hash(fname)
            self.cache[key] = {
                "mtime": mtime,
                "size": size,
                "hash": content_hash,
                "data": tags,
            }

        return tags, error

    def _compute_file_hash(self, fname: str) -> str | None:
        """Compute a fast hash of file contents using xxhash if available, else md5."""
        try:
            # Try xxhash first (much faster)
            try:
                import xxhash
                hasher = xxhash.xxh64()
            except ImportError:
                hasher = hashlib.md5()

            with open(fname, "rb") as f:
                # Read in chunks for large files
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except (OSError, IOError):
            return None

    def get_extraction_stats(self) -> tuple[int, int]:
        """Return (cache_hits, cache_misses) counts in a thread-safe manner."""
        with self._lock:
            return self._cache_hits, self._cache_misses

    def set_known_definitions(self, definitions: set[str]) -> None:
        """Set known definitions for filtering Pygments backfill."""
        self._known_definitions = definitions

    def _get_tags_raw(
        self, fname: str, rel_fname: str
    ) -> tuple[list[Tag], ExtractionError | None]:
        """Extract tags from a file without caching."""
        lang = filename_to_lang(fname)
        if not lang:
            # Not an error - just unsupported language
            return [], None

        try:
            language = get_language(lang)
            parser = get_parser(lang)
        except Exception as e:
            return [], ExtractionError(
                fname=fname,
                error_type="ParserError",
                message=f"Failed to get parser for {lang}: {e}",
            )

        try:
            with open(fname, "r", encoding="utf-8", errors="ignore") as f:
                code = f.read()
        except Exception as e:
            return [], ExtractionError(
                fname=fname,
                error_type="ReadError",
                message=str(e),
            )

        try:
            tree = parser.parse(bytes(code, "utf-8"))
        except Exception as e:
            return [], ExtractionError(
                fname=fname,
                error_type="ParseError",
                message=f"tree-sitter parse failed: {e}",
            )

        # Load query
        query_path = Path(__file__).parent / "queries" / f"{lang}.scm"
        if not query_path.exists():
            # Not an error - just no query file for this language
            return [], None

        try:
            with open(query_path, "r", encoding="utf-8") as f:
                query_scm = f.read()

            query = language.query(query_scm)
            cursor = tree_sitter.QueryCursor(query)
            captures_map = cursor.captures(tree.root_node)

            # Flatten dict to list of (node, name) and sort by position
            captures = []
            for name, nodes in captures_map.items():
                for node in nodes:
                    captures.append((node, name))

            captures.sort(key=lambda x: x[0].start_byte)

        except Exception as e:
            return [], ExtractionError(
                fname=fname,
                error_type="QueryError",
                message=f"tree-sitter query failed: {e}",
            )

        tags: list[Tag] = []
        has_def = False
        has_ref = False

        # Build a map of class/function scopes for parent tracking
        scope_map = self._build_scope_map(tree.root_node, lang)

        for node, tag_name in captures:
            kind = None
            if tag_name.startswith("name.definition."):
                kind = "def"
                has_def = True
            elif tag_name.startswith("name.reference."):
                kind = "ref"
                has_ref = True
            else:
                continue

            # Find parent scope for this node
            parent_name = self._find_parent_scope(node, scope_map)

            tags.append(Tag(
                rel_fname=rel_fname,
                fname=fname,
                line=node.start_point[0],
                name=node.text.decode("utf-8"),
                kind=kind,
                parent=parent_name,
            ))

        # Backfill references if we found definitions but no references
        # Apply strict filtering to reduce noise
        if has_def and not has_ref:
            tags.extend(self._backfill_references(fname, rel_fname, code))

        return tags, None

    def _build_scope_map(
        self, root_node, lang: str
    ) -> list[tuple[int, int, str, str]]:
        """Build a map of (start_byte, end_byte, name, type) for class/function scopes."""
        scopes: list[tuple[int, int, str, str]] = []

        # Node types that define scopes by language
        scope_types = {
            "python": {"class_definition", "function_definition"},
            "javascript": {"class_declaration", "function_declaration", "method_definition", "arrow_function"},
            "typescript": {"class_declaration", "function_declaration", "method_definition", "arrow_function"},
            "go": {"function_declaration", "method_declaration", "type_declaration"},
        }

        name_child_types = {
            "python": {"class_definition": "name", "function_definition": "name"},
            "javascript": {"class_declaration": "name", "function_declaration": "name", "method_definition": "name"},
            "typescript": {"class_declaration": "name", "function_declaration": "name", "method_definition": "name"},
            "go": {"function_declaration": "name", "method_declaration": "name", "type_declaration": "name"},
        }

        types_to_check = scope_types.get(lang, set())
        name_fields = name_child_types.get(lang, {})

        def walk(node):
            if node.type in types_to_check:
                name_field = name_fields.get(node.type, "name")
                name_node = node.child_by_field_name(name_field)
                if name_node:
                    name = name_node.text.decode("utf-8")
                    scopes.append((node.start_byte, node.end_byte, name, node.type))

            for child in node.children:
                walk(child)

        walk(root_node)
        return scopes

    def _find_parent_scope(
        self, node, scope_map: list[tuple[int, int, str, str]]
    ) -> str | None:
        """Find the innermost parent scope containing this node."""
        node_start = node.start_byte
        node_end = node.end_byte

        # Find all scopes containing this node
        containing_scopes = [
            (start, end, name, stype)
            for start, end, name, stype in scope_map
            if start <= node_start and node_end <= end
            # Exclude the node itself (when the node IS the scope definition)
            and not (start == node_start and name == node.text.decode("utf-8"))
        ]

        if not containing_scopes:
            return None

        # Return the innermost (smallest) scope
        containing_scopes.sort(key=lambda x: x[1] - x[0])
        return containing_scopes[0][2]

    def _backfill_references(
        self, fname: str, rel_fname: str, code: str
    ) -> list[Tag]:
        """Backfill references using Pygments with strict filtering."""
        backfill_tags: list[Tag] = []
        try:
            from pygments.lexers import get_lexer_for_filename
            from pygments.token import Token

            lexer = get_lexer_for_filename(fname)
            tokens = lexer.get_tokens(code)
            seen: set[str] = set()

            for token_type, token_text in tokens:
                if token_type not in Token.Name:
                    continue

                # Filter 1: Minimum length of 4 characters
                if len(token_text) < 4:
                    continue

                # Filter 2: Not in stopwords
                if token_text.lower() in _BACKFILL_STOPWORDS:
                    continue

                # Filter 3: Deduplicate within file
                if token_text in seen:
                    continue
                seen.add(token_text)

                # Filter 4: If we have known definitions, only include matches
                if self._known_definitions and token_text not in self._known_definitions:
                    continue

                backfill_tags.append(Tag(
                    rel_fname=rel_fname,
                    fname=fname,
                    line=-1,
                    name=token_text,
                    kind="ref",
                ))
        except Exception:
            # Pygments failure is not critical
            pass

        return backfill_tags
