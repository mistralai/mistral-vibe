"""Pydantic schemas for structured repository map output.

Defines comprehensive schemas for:
- Directory hierarchy with rollups (file count, LOC, language, test/generated flags)
- Symbol index with relationships (imports, inheritance, calls)
- Dependency graph with node/edge attributes for JSON/GraphML/GEXF export
- Entrypoints and core modules detection
- Incremental indexing metadata
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime
from enum import StrEnum, auto
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, computed_field


class FileType(StrEnum):
    """Classification of file types for rollup purposes."""

    SOURCE = auto()
    TEST = auto()
    GENERATED = auto()
    CONFIG = auto()
    DOC = auto()
    OTHER = auto()


class Language(StrEnum):
    """Supported programming languages."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    CPP = "cpp"
    C = "c"
    RUBY = "ruby"
    SHELL = "shell"
    YAML = "yaml"
    JSON = "json"
    MARKDOWN = "markdown"
    OTHER = "other"


class SymbolKind(StrEnum):
    """Kind of symbol in the codebase."""

    CLASS = auto()
    FUNCTION = auto()
    METHOD = auto()
    VARIABLE = auto()
    CONSTANT = auto()
    IMPORT = auto()
    MODULE = auto()
    INTERFACE = auto()
    TYPE = auto()
    ENUM = auto()


class RelationshipKind(StrEnum):
    """Kind of relationship between symbols."""

    IMPORTS = auto()
    INHERITS = auto()
    CALLS = auto()
    DEFINES = auto()
    REFERENCES = auto()
    IMPLEMENTS = auto()


# =============================================================================
# File-Level Schemas
# =============================================================================


class FileMetrics(BaseModel):
    """Metrics for a single file."""

    path: str = Field(description="Relative path from repository root")
    language: Language = Field(default=Language.OTHER)
    file_type: FileType = Field(default=FileType.SOURCE)
    lines_of_code: int = Field(default=0, ge=0)
    blank_lines: int = Field(default=0, ge=0)
    comment_lines: int = Field(default=0, ge=0)
    size_bytes: int = Field(default=0, ge=0)
    hash: str = Field(default="", description="Content hash for change detection")
    last_modified: datetime | None = Field(default=None)

    @computed_field
    @property
    def total_lines(self) -> int:
        """Total lines in file."""
        return self.lines_of_code + self.blank_lines + self.comment_lines


class FileHash(BaseModel):
    """File hash entry for incremental indexing."""

    path: str
    content_hash: str
    mtime: float
    size: int

    @classmethod
    def from_path(cls, path: Path, root: Path | None = None) -> FileHash | None:
        """Create FileHash from a file path."""
        if not path.is_file():
            return None
        try:
            stat = path.stat()
            content = path.read_bytes()
            content_hash = hashlib.sha256(content).hexdigest()[:16]
            rel_path = str(path.relative_to(root)) if root else str(path)
            return cls(
                path=rel_path,
                content_hash=content_hash,
                mtime=stat.st_mtime,
                size=stat.st_size,
            )
        except (OSError, ValueError):
            return None


# =============================================================================
# Directory-Level Schemas
# =============================================================================


class DirectoryRollup(BaseModel):
    """Hierarchical directory summary with rollup statistics."""

    path: str = Field(description="Relative path from repository root")
    name: str = Field(description="Directory name")
    file_count: int = Field(default=0, ge=0)
    total_loc: int = Field(default=0, ge=0, description="Total lines of code")
    primary_language: Language | None = Field(
        default=None, description="Most common language in directory"
    )
    language_breakdown: dict[str, int] = Field(
        default_factory=dict, description="LOC per language"
    )
    is_test_directory: bool = Field(default=False)
    is_generated: bool = Field(default=False)
    subdirectories: list[DirectoryRollup] = Field(default_factory=list)
    files: list[FileMetrics] = Field(default_factory=list)

    @computed_field
    @property
    def recursive_file_count(self) -> int:
        """Total files including subdirectories."""
        return self.file_count + sum(
            d.recursive_file_count for d in self.subdirectories
        )

    @computed_field
    @property
    def recursive_loc(self) -> int:
        """Total LOC including subdirectories."""
        return self.total_loc + sum(d.recursive_loc for d in self.subdirectories)


# =============================================================================
# Symbol-Level Schemas
# =============================================================================


class SymbolLocation(BaseModel):
    """Location of a symbol in the codebase."""

    file: str = Field(description="Relative file path")
    line: int = Field(ge=1)
    column: int = Field(default=0, ge=0)
    end_line: int | None = Field(default=None)


class Symbol(BaseModel):
    """A symbol (class, function, etc.) in the codebase."""

    name: str
    kind: SymbolKind
    location: SymbolLocation
    parent: str | None = Field(
        default=None, description="Parent symbol name (e.g., class for method)"
    )
    signature: str | None = Field(
        default=None, description="Function/method signature if available"
    )
    docstring: str | None = Field(default=None, description="First line of docstring")
    is_exported: bool = Field(
        default=True, description="Whether symbol is public/exported"
    )
    is_async: bool = Field(default=False)

    @computed_field
    @property
    def qualified_name(self) -> str:
        """Fully qualified name including parent."""
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name


class SymbolRelationship(BaseModel):
    """A relationship between two symbols."""

    source: str = Field(description="Source symbol qualified name or file")
    target: str = Field(description="Target symbol qualified name or file")
    kind: RelationshipKind
    weight: float = Field(default=1.0, ge=0.0, description="Relationship strength")
    context: str | None = Field(
        default=None, description="Additional context (e.g., import path)"
    )


class FileSymbolIndex(BaseModel):
    """Symbol index for a single file."""

    file: str = Field(description="Relative file path")
    symbols: list[Symbol] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list, description="Imported modules")
    exports: list[str] = Field(
        default_factory=list, description="Exported symbol names"
    )

    @computed_field
    @property
    def class_count(self) -> int:
        """Number of classes defined."""
        return sum(1 for s in self.symbols if s.kind == SymbolKind.CLASS)

    @computed_field
    @property
    def function_count(self) -> int:
        """Number of functions/methods defined."""
        return sum(
            1
            for s in self.symbols
            if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
        )


# =============================================================================
# Graph Schemas
# =============================================================================


class GraphNode(BaseModel):
    """A node in the dependency graph."""

    id: str = Field(description="Unique node identifier (file path or symbol name)")
    label: str = Field(description="Display label")
    node_type: str = Field(description="Type: 'file', 'module', 'class', 'function'")
    language: Language | None = Field(default=None)
    loc: int = Field(default=0, description="Lines of code")
    is_test: bool = Field(default=False)
    is_entrypoint: bool = Field(default=False)
    is_core: bool = Field(default=False, description="Part of core modules")
    pagerank: float = Field(default=0.0, description="PageRank score")
    in_degree: int = Field(default=0, description="Number of incoming edges")
    out_degree: int = Field(default=0, description="Number of outgoing edges")
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """An edge in the dependency graph."""

    source: str = Field(description="Source node ID")
    target: str = Field(description="Target node ID")
    edge_type: str = Field(
        description="Type: 'imports', 'calls', 'inherits', 'references'"
    )
    weight: float = Field(default=1.0, ge=0.0)
    symbol: str | None = Field(
        default=None, description="Symbol involved in relationship"
    )


class DependencyGraph(BaseModel):
    """Complete dependency graph with nodes and edges."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def node_count(self) -> int:
        """Number of nodes."""
        return len(self.nodes)

    @computed_field
    @property
    def edge_count(self) -> int:
        """Number of edges."""
        return len(self.edges)

    def get_node(self, node_id: str) -> GraphNode | None:
        """Get node by ID."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None


# =============================================================================
# Entrypoint and Core Module Schemas
# =============================================================================


class EntrypointType(StrEnum):
    """Type of entrypoint."""

    CLI = auto()
    API = auto()
    SERVICE = auto()
    SCRIPT = auto()
    TEST_RUNNER = auto()
    WEB = auto()
    LIBRARY = auto()


class Entrypoint(BaseModel):
    """An entrypoint into the codebase."""

    name: str = Field(description="Entrypoint name")
    file: str = Field(description="Relative file path")
    entrypoint_type: EntrypointType
    description: str | None = Field(default=None)
    command: str | None = Field(
        default=None, description="CLI command or script invocation"
    )
    exports: list[str] = Field(
        default_factory=list, description="Main exported symbols"
    )


class CoreModule(BaseModel):
    """A core module in the codebase."""

    name: str = Field(description="Module name")
    path: str = Field(description="Module path (file or directory)")
    description: str | None = Field(default=None)
    importance_score: float = Field(
        default=0.0, description="PageRank-derived importance"
    )
    dependents_count: int = Field(default=0, description="Number of files that use it")
    dependencies_count: int = Field(default=0, description="Number of dependencies")
    is_hotspot: bool = Field(
        default=False, description="High coupling/churn indicator"
    )


# =============================================================================
# Incremental Indexing Schemas
# =============================================================================


class IncrementalState(BaseModel):
    """State for incremental indexing."""

    version: str = Field(default="1.0.0", description="Schema version")
    last_indexed: datetime = Field(default_factory=datetime.now)
    git_commit: str | None = Field(
        default=None, description="Git commit hash at last index"
    )
    file_hashes: dict[str, FileHash] = Field(
        default_factory=dict, description="File path -> hash mapping"
    )
    indexed_files: set[str] = Field(default_factory=set)

    def get_changed_files(
        self, current_hashes: dict[str, FileHash]
    ) -> tuple[set[str], set[str], set[str]]:
        """Compute changed, added, and removed files.

        Returns:
            Tuple of (modified, added, removed) file paths.
        """
        previous = set(self.file_hashes.keys())
        current = set(current_hashes.keys())

        added = current - previous
        removed = previous - current
        modified = set()

        for path in current & previous:
            if self.file_hashes[path].content_hash != current_hashes[path].content_hash:
                modified.add(path)

        return modified, added, removed


# =============================================================================
# Complete Repository Map Schema
# =============================================================================


class RepositoryMapSummary(BaseModel):
    """Human-readable summary section of the repository map."""

    name: str = Field(default="", description="Repository name")
    description: str | None = Field(default=None)
    total_files: int = Field(default=0)
    total_loc: int = Field(default=0)
    primary_language: Language | None = Field(default=None)
    language_stats: dict[str, int] = Field(
        default_factory=dict, description="LOC per language"
    )
    test_coverage_estimate: float | None = Field(
        default=None, description="Ratio of test files to source files"
    )


class RepositoryMap(BaseModel):
    """Complete repository map with all components."""

    # Metadata
    version: str = Field(default="1.0.0", description="Schema version")
    generated_at: datetime = Field(default_factory=datetime.now)
    repository_root: str = Field(description="Absolute path to repository root")

    # Summary
    summary: RepositoryMapSummary = Field(default_factory=lambda: RepositoryMapSummary())

    # Directory hierarchy with rollups
    directory_tree: DirectoryRollup | None = Field(default=None)

    # Symbol index
    symbol_index: list[FileSymbolIndex] = Field(default_factory=list)
    relationships: list[SymbolRelationship] = Field(default_factory=list)

    # Dependency graph
    dependency_graph: DependencyGraph = Field(default_factory=DependencyGraph)

    # Entrypoints and core modules
    entrypoints: list[Entrypoint] = Field(default_factory=list)
    core_modules: list[CoreModule] = Field(default_factory=list)

    # Incremental state
    incremental_state: IncrementalState = Field(default_factory=IncrementalState)

    def get_file_symbols(self, file_path: str) -> FileSymbolIndex | None:
        """Get symbol index for a specific file."""
        for idx in self.symbol_index:
            if idx.file == file_path:
                return idx
        return None

    def find_symbol(self, name: str) -> list[Symbol]:
        """Find all symbols matching a name."""
        results = []
        for idx in self.symbol_index:
            for symbol in idx.symbols:
                if symbol.name == name or symbol.qualified_name == name:
                    results.append(symbol)
        return results


# =============================================================================
# Export/Serialization Helpers
# =============================================================================


def detect_primary_language(loc_by_lang: dict[str, int]) -> Language | None:
    """Detect primary language from LOC breakdown."""
    if not loc_by_lang:
        return None
    most_common = Counter(loc_by_lang).most_common(1)
    if most_common:
        lang_str = most_common[0][0].lower()
        try:
            return Language(lang_str)
        except ValueError:
            return Language.OTHER
    return None
