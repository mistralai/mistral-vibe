"""Comprehensive repository map generator.

Generates a complete repository map with:
- Hierarchical directory summaries with rollups
- Symbol index with relationships
- Dependency graph artifacts (JSON/GraphML/GEXF)
- Entrypoint detection (CLI, API, services)
- Core modules identification
- Incremental indexing support via git-diff + file hashes

Usage:
    from vibe.repomap.generator import RepositoryMapGenerator

    generator = RepositoryMapGenerator(root=Path.cwd())
    repo_map = generator.generate()

    # Export artifacts
    generator.export_artifacts(output_dir=Path("./artifacts"))
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx

from .analyzer import DirectoryAnalyzer, analyze_directory, detect_language
from .core import RepoMap, extract_mentions_from_text
from .discovery import discover_files, SUPPORTED_EXTENSIONS
from .exporter import (
    build_networkx_graph,
    compute_graph_statistics,
    export_all_formats,
    export_gexf,
    export_graphml,
    export_json,
    from_networkx_graph,
)
from .graph import build_graph, distribute_rank, rank_files
from .schemas import (
    CoreModule,
    DependencyGraph,
    DirectoryRollup,
    Entrypoint,
    EntrypointType,
    FileHash,
    FileSymbolIndex,
    GraphEdge,
    GraphNode,
    IncrementalState,
    Language,
    RepositoryMap,
    RepositoryMapSummary,
    Symbol,
    SymbolKind,
    SymbolLocation,
    SymbolRelationship,
    RelationshipKind,
    detect_primary_language,
)
from .tags import Tag, TagExtractor


# =============================================================================
# Entrypoint Detection Patterns
# =============================================================================

_CLI_PATTERNS = [
    r"if\s+__name__\s*==\s*['\"]__main__['\"]",
    r"@click\.command",
    r"@app\.command",
    r"argparse\.ArgumentParser",
    r"typer\.Typer",
    r"fire\.Fire",
]

_API_PATTERNS = [
    r"@app\.route",
    r"@router\.",
    r"@api\.",
    r"FastAPI\(",
    r"Flask\(",
    r"Django",
    r"@Get\(",
    r"@Post\(",
]

_SERVICE_PATTERNS = [
    r"async\s+def\s+main\s*\(",
    r"asyncio\.run\(",
    r"uvicorn\.run",
    r"serve\s*\(",
]

_COMPILED_PATTERNS: dict[EntrypointType, list[re.Pattern[str]]] = {
    EntrypointType.CLI: [re.compile(p, re.MULTILINE) for p in _CLI_PATTERNS],
    EntrypointType.API: [re.compile(p, re.MULTILINE) for p in _API_PATTERNS],
    EntrypointType.SERVICE: [re.compile(p, re.MULTILINE) for p in _SERVICE_PATTERNS],
}


def detect_entrypoint_type(content: str, file_path: Path) -> EntrypointType | None:
    """Detect entrypoint type from file content and path."""
    # Check file patterns first
    stem = file_path.stem.lower()

    if stem in ("main", "app", "cli", "server", "run", "__main__"):
        # Could be CLI, API, or service - check content
        pass
    elif stem.startswith("test_") or stem.endswith("_test"):
        return EntrypointType.TEST_RUNNER

    # Check content patterns
    for ep_type, patterns in _COMPILED_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(content):
                return ep_type

    # Check for __main__.py
    if file_path.name == "__main__.py":
        return EntrypointType.CLI

    return None


# =============================================================================
# Repository Map Generator
# =============================================================================


class RepositoryMapGenerator:
    """Generates comprehensive repository maps with all components."""

    def __init__(
        self,
        root: Path,
        cache_dir: Path | None = None,
        exclude_patterns: set[str] | None = None,
        include_hidden: bool = False,
    ):
        """Initialize the generator.

        Args:
            root: Repository root directory
            cache_dir: Optional cache directory for tags
            exclude_patterns: Patterns to exclude from analysis
            include_hidden: Whether to include hidden files
        """
        self.root = root.resolve()
        self.cache_dir = cache_dir
        self.exclude_patterns = exclude_patterns or {
            ".git",
            ".hg",
            ".svn",
            "node_modules",
            "__pycache__",
            ".tox",
            ".nox",
            "venv",
            ".venv",
            "env",
            ".env",
            "site-packages",
            "dist",
            "build",
            ".idea",
            ".vscode",
            "test_env",
        }
        self.include_hidden = include_hidden

        # State
        self._repo_map: RepositoryMap | None = None
        self._nx_graph: nx.MultiDiGraph | None = None
        self._tags: list[Tag] = []
        self._incremental_state: IncrementalState | None = None

    def generate(
        self,
        incremental: bool = False,
        previous_state: IncrementalState | None = None,
    ) -> RepositoryMap:
        """Generate the complete repository map.

        Args:
            incremental: Whether to use incremental indexing
            previous_state: Previous state for incremental updates

        Returns:
            Complete RepositoryMap with all components
        """
        self._incremental_state = previous_state

        # Phase 1: Directory analysis
        directory_tree = self._analyze_directories()

        # Phase 2: Discover files
        files = self._discover_files()

        # Phase 3: Extract tags and build symbol index
        symbol_index, relationships = self._build_symbol_index(files)

        # Phase 4: Build dependency graph
        dependency_graph = self._build_dependency_graph(files)

        # Phase 5: Detect entrypoints
        entrypoints = self._detect_entrypoints(files)

        # Phase 6: Identify core modules
        core_modules = self._identify_core_modules(dependency_graph)

        # Phase 7: Build summary
        summary = self._build_summary(directory_tree)

        # Phase 8: Build incremental state
        incremental_state = self._build_incremental_state(files)

        self._repo_map = RepositoryMap(
            repository_root=str(self.root),
            summary=summary,
            directory_tree=directory_tree,
            symbol_index=symbol_index,
            relationships=relationships,
            dependency_graph=dependency_graph,
            entrypoints=entrypoints,
            core_modules=core_modules,
            incremental_state=incremental_state,
        )

        return self._repo_map

    def _analyze_directories(self) -> DirectoryRollup:
        """Phase 1: Analyze directory structure with rollups."""
        analyzer = DirectoryAnalyzer(
            root=self.root,
            exclude_patterns=self.exclude_patterns,
            include_hidden=self.include_hidden,
        )
        return analyzer.analyze()

    def _discover_files(self) -> list[Path]:
        """Phase 2: Discover all source files."""
        # Note: We use respect_gitignore=False because our exclusion patterns
        # already handle common exclusions, and gitignore can be too aggressive
        # for repo mapping purposes (e.g., ignoring lib/ which may have sources)
        result = discover_files(
            root=self.root,
            extensions=SUPPORTED_EXTENSIONS,
            respect_gitignore=False,
        )
        return [Path(f) for f in result.files]

    def _build_symbol_index(
        self, files: list[Path]
    ) -> tuple[list[FileSymbolIndex], list[SymbolRelationship]]:
        """Phase 3: Extract tags and build symbol index with relationships."""
        symbol_indices: list[FileSymbolIndex] = []
        relationships: list[SymbolRelationship] = []

        # Use TagExtractor from existing infrastructure
        cache_dir = str(self.cache_dir) if self.cache_dir else None
        extractor = TagExtractor(cache_dir=cache_dir, project_root=str(self.root))

        self._tags = []

        for file_path in files:
            try:
                rel_path = str(file_path.relative_to(self.root))
            except ValueError:
                rel_path = str(file_path)

            tags, error = extractor.get_tags(str(file_path), rel_path)
            if error:
                continue

            self._tags.extend(tags)

            # Group tags by kind
            symbols: list[Symbol] = []
            imports: list[str] = []
            exports: list[str] = []

            for tag in tags:
                if tag.kind == "def":
                    # Convert to Symbol
                    kind = self._tag_to_symbol_kind(tag)
                    symbol = Symbol(
                        name=tag.name,
                        kind=kind,
                        location=SymbolLocation(
                            file=rel_path,
                            line=max(1, tag.line),
                        ),
                        parent=tag.parent,
                        is_exported=not tag.name.startswith("_"),
                    )
                    symbols.append(symbol)

                    if symbol.is_exported:
                        exports.append(tag.name)

                elif tag.kind == "ref":
                    # Track imports for relationships
                    if tag.name not in imports:
                        imports.append(tag.name)

            if symbols or imports:
                symbol_indices.append(
                    FileSymbolIndex(
                        file=rel_path,
                        symbols=symbols,
                        imports=imports,
                        exports=exports,
                    )
                )

        # Build relationships from tags
        relationships = self._extract_relationships(self._tags, symbol_indices)

        return symbol_indices, relationships

    def _tag_to_symbol_kind(self, tag: Tag) -> SymbolKind:
        """Convert tag to symbol kind based on naming conventions."""
        name = tag.name

        # Check parent for method detection
        if tag.parent:
            return SymbolKind.METHOD

        # Check naming conventions
        if name[0].isupper() and "_" not in name:
            return SymbolKind.CLASS
        if name.isupper():
            return SymbolKind.CONSTANT

        return SymbolKind.FUNCTION

    def _extract_relationships(
        self, tags: list[Tag], symbol_indices: list[FileSymbolIndex]
    ) -> list[SymbolRelationship]:
        """Extract relationships between symbols."""
        relationships: list[SymbolRelationship] = []

        # Build definition map
        definitions: dict[str, set[str]] = defaultdict(set)  # name -> files
        for tag in tags:
            if tag.kind == "def":
                definitions[tag.name].add(tag.fname)

        # Build reference map
        references: dict[str, list[str]] = defaultdict(list)  # name -> files
        for tag in tags:
            if tag.kind == "ref":
                references[tag.name].append(tag.fname)

        # Create import relationships
        for name, ref_files in references.items():
            if name not in definitions:
                continue

            def_files = definitions[name]
            for ref_file in set(ref_files):
                for def_file in def_files:
                    if ref_file == def_file:
                        continue  # Skip self-references

                    try:
                        ref_rel = str(Path(ref_file).relative_to(self.root))
                        def_rel = str(Path(def_file).relative_to(self.root))
                    except ValueError:
                        continue

                    relationships.append(
                        SymbolRelationship(
                            source=ref_rel,
                            target=def_rel,
                            kind=RelationshipKind.IMPORTS,
                            context=name,
                        )
                    )

        return relationships

    def _build_dependency_graph(self, files: list[Path]) -> DependencyGraph:
        """Phase 4: Build dependency graph from tags."""
        if not self._tags:
            return DependencyGraph()

        # Use existing graph building infrastructure
        self._nx_graph = build_graph(
            self._tags,
            chat_files=set(),
            mentioned_fnames=set(),
            mentioned_idents=set(),
        )

        # Compute PageRank (with fallback if numpy not available)
        try:
            pagerank = nx.pagerank(self._nx_graph, weight="weight")
        except (nx.PowerIterationFailedConvergence, ZeroDivisionError, ImportError):
            # Fallback to uniform distribution if PageRank fails
            num_nodes = len(self._nx_graph)
            pagerank = {n: 1.0 / num_nodes for n in self._nx_graph.nodes()} if num_nodes > 0 else {}

        # Convert to DependencyGraph schema
        nodes: list[GraphNode] = []
        for node_id in self._nx_graph.nodes():
            try:
                rel_path = str(Path(node_id).relative_to(self.root))
            except ValueError:
                rel_path = str(node_id)

            file_path = Path(node_id)
            lang = detect_language(file_path)

            # Count LOC for the file
            loc = 0
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    loc = len(content.splitlines())
                except OSError:
                    pass

            nodes.append(
                GraphNode(
                    id=rel_path,
                    label=file_path.name,
                    node_type="file",
                    language=lang if lang != Language.OTHER else None,
                    loc=loc,
                    is_test=self._is_test_file(file_path),
                    pagerank=pagerank.get(node_id, 0.0),
                    in_degree=self._nx_graph.in_degree(node_id),
                    out_degree=self._nx_graph.out_degree(node_id),
                )
            )

        # Convert edges
        edges: list[GraphEdge] = []
        for source, target, data in self._nx_graph.edges(data=True):
            try:
                source_rel = str(Path(source).relative_to(self.root))
                target_rel = str(Path(target).relative_to(self.root))
            except ValueError:
                continue

            edges.append(
                GraphEdge(
                    source=source_rel,
                    target=target_rel,
                    edge_type="references",
                    weight=data.get("weight", 1.0),
                    symbol=data.get("ident"),
                )
            )

        return DependencyGraph(nodes=nodes, edges=edges)

    def _is_test_file(self, path: Path) -> bool:
        """Check if a file is a test file."""
        path_str = str(path).lower()
        stem = path.stem.lower()
        return (
            "test" in path_str
            or stem.startswith("test_")
            or stem.endswith("_test")
            or stem.endswith("_spec")
        )

    def _detect_entrypoints(self, files: list[Path]) -> list[Entrypoint]:
        """Phase 5: Detect entrypoints in the codebase."""
        entrypoints: list[Entrypoint] = []

        for file_path in files:
            # Check for known entrypoint patterns
            if not file_path.suffix in {".py", ".js", ".ts"}:
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                rel_path = str(file_path.relative_to(self.root))
            except (OSError, ValueError):
                continue

            ep_type = detect_entrypoint_type(content, file_path)
            if ep_type:
                # Extract exports
                exports: list[str] = []
                for tag in self._tags:
                    if tag.fname == str(file_path) and tag.kind == "def":
                        if not tag.name.startswith("_"):
                            exports.append(tag.name)

                entrypoints.append(
                    Entrypoint(
                        name=file_path.stem,
                        file=rel_path,
                        entrypoint_type=ep_type,
                        exports=exports[:10],  # Limit exports
                    )
                )

        return entrypoints

    def _identify_core_modules(
        self, dependency_graph: DependencyGraph
    ) -> list[CoreModule]:
        """Phase 6: Identify core modules based on PageRank and connectivity."""
        if not dependency_graph.nodes:
            return []

        # Sort by PageRank
        sorted_nodes = sorted(
            dependency_graph.nodes, key=lambda n: n.pagerank, reverse=True
        )

        # Take top 10% as core modules (minimum 5, maximum 20)
        num_core = max(5, min(20, len(sorted_nodes) // 10))
        top_nodes = sorted_nodes[:num_core]

        # Exclude test files from core modules
        core_modules: list[CoreModule] = []
        for node in top_nodes:
            if node.is_test:
                continue

            # Calculate hotspot score
            is_hotspot = node.in_degree > 5 and node.out_degree > 5

            core_modules.append(
                CoreModule(
                    name=Path(node.id).stem,
                    path=node.id,
                    importance_score=node.pagerank,
                    dependents_count=node.in_degree,
                    dependencies_count=node.out_degree,
                    is_hotspot=is_hotspot,
                )
            )

        # Mark core nodes in the graph
        core_paths = {m.path for m in core_modules}
        for node in dependency_graph.nodes:
            node.is_core = node.id in core_paths

        return core_modules

    def _build_summary(self, directory_tree: DirectoryRollup) -> RepositoryMapSummary:
        """Phase 7: Build human-readable summary."""
        return RepositoryMapSummary(
            name=self.root.name,
            total_files=directory_tree.recursive_file_count,
            total_loc=directory_tree.recursive_loc,
            primary_language=directory_tree.primary_language,
            language_stats=directory_tree.language_breakdown,
        )

    def _build_incremental_state(self, files: list[Path]) -> IncrementalState:
        """Phase 8: Build state for incremental indexing."""
        file_hashes: dict[str, FileHash] = {}

        for file_path in files:
            if file_hash := FileHash.from_path(file_path, self.root):
                file_hashes[file_hash.path] = file_hash

        # Try to get git commit
        git_commit = self._get_git_commit()

        return IncrementalState(
            last_indexed=datetime.now(),
            git_commit=git_commit,
            file_hashes=file_hashes,
            indexed_files=set(file_hashes.keys()),
        )

    def _get_git_commit(self) -> str | None:
        """Get current git commit hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()[:12]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def export_artifacts(
        self,
        output_dir: Path,
        formats: set[str] | None = None,
    ) -> dict[str, Path]:
        """Export repository map to various formats.

        Args:
            output_dir: Directory to write artifacts
            formats: Set of formats to export ("json", "graphml", "gexf", "markdown")
                     Default exports all formats.

        Returns:
            Dict mapping format name to output path
        """
        if not self._repo_map:
            raise ValueError("Generate repository map first with generate()")

        output_dir.mkdir(parents=True, exist_ok=True)
        formats = formats or {"json", "graphml", "gexf", "markdown"}

        paths: dict[str, Path] = {}

        # Export full repository map as JSON
        if "json" in formats:
            json_path = output_dir / "repository_map.json"
            json_path.write_text(
                self._repo_map.model_dump_json(indent=2), encoding="utf-8"
            )
            paths["json"] = json_path

        # Export dependency graph in various formats
        if "graphml" in formats:
            graphml_path = output_dir / "dependency_graph.graphml"
            export_graphml(self._repo_map.dependency_graph, graphml_path)
            paths["graphml"] = graphml_path

        if "gexf" in formats:
            gexf_path = output_dir / "dependency_graph.gexf"
            export_gexf(self._repo_map.dependency_graph, gexf_path)
            paths["gexf"] = gexf_path

        # Export human-readable Markdown
        if "markdown" in formats:
            md_path = output_dir / "REPOSITORY_MAP.md"
            md_path.write_text(self.render_markdown(), encoding="utf-8")
            paths["markdown"] = md_path

        # Export incremental state
        state_path = output_dir / ".repomap_state.json"
        state_path.write_text(
            self._repo_map.incremental_state.model_dump_json(indent=2),
            encoding="utf-8",
        )
        paths["state"] = state_path

        return paths

    def render_markdown(self) -> str:
        """Render repository map as concise Markdown."""
        if not self._repo_map:
            raise ValueError("Generate repository map first with generate()")

        lines: list[str] = []
        rm = self._repo_map

        # Header
        lines.append(f"# Repository Map: {rm.summary.name}")
        lines.append("")
        lines.append(f"*Generated: {rm.generated_at.strftime('%Y-%m-%d %H:%M')}*")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Total Files:** {rm.summary.total_files}")
        lines.append(f"- **Total LOC:** {rm.summary.total_loc:,}")
        if rm.summary.primary_language:
            lines.append(f"- **Primary Language:** {rm.summary.primary_language.value}")
        lines.append("")

        # Language breakdown
        if rm.summary.language_stats:
            lines.append("### Language Breakdown")
            lines.append("")
            lines.append("| Language | LOC | % |")
            lines.append("|----------|-----|---|")
            total = sum(rm.summary.language_stats.values())
            for lang, loc in sorted(
                rm.summary.language_stats.items(), key=lambda x: -x[1]
            )[:10]:
                pct = (loc / total * 100) if total > 0 else 0
                lines.append(f"| {lang} | {loc:,} | {pct:.1f}% |")
            lines.append("")

        # Entrypoints
        if rm.entrypoints:
            lines.append("## Entrypoints")
            lines.append("")
            for ep in rm.entrypoints[:10]:
                lines.append(f"- **{ep.name}** (`{ep.file}`) - {ep.entrypoint_type.value}")
            lines.append("")

        # Core Modules
        if rm.core_modules:
            lines.append("## Core Modules")
            lines.append("")
            lines.append("Modules ranked by importance (PageRank):")
            lines.append("")
            for cm in rm.core_modules[:10]:
                hotspot = " 🔥" if cm.is_hotspot else ""
                lines.append(
                    f"- **{cm.name}** (`{cm.path}`) - "
                    f"score: {cm.importance_score:.4f}, "
                    f"↓{cm.dependents_count} ↑{cm.dependencies_count}{hotspot}"
                )
            lines.append("")

        # Directory Structure (top-level only)
        if rm.directory_tree:
            lines.append("## Directory Structure")
            lines.append("")
            lines.append("```")
            self._render_directory_tree(rm.directory_tree, lines, depth=0, max_depth=2)
            lines.append("```")
            lines.append("")

        # Graph Statistics
        if rm.dependency_graph.nodes:
            stats = compute_graph_statistics(rm.dependency_graph)
            lines.append("## Dependency Graph Statistics")
            lines.append("")
            lines.append(f"- **Nodes:** {stats.get('node_count', 0)}")
            lines.append(f"- **Edges:** {stats.get('edge_count', 0)}")
            lines.append(f"- **Density:** {stats.get('density', 0):.4f}")
            lines.append(f"- **Components:** {stats.get('num_components', 0)}")
            lines.append("")

        # Incremental Update Instructions
        lines.append("## Incremental Update Instructions")
        lines.append("")
        lines.append("To update incrementally:")
        lines.append("")
        lines.append("1. Use `git diff --name-only <last-commit>` to find changed files")
        lines.append("2. Compare file hashes in `.repomap_state.json` with current files")
        lines.append("3. Reindex only changed files and their reverse dependencies")
        lines.append("4. Merge updated symbols/edges into existing graph")
        lines.append("")
        if rm.incremental_state.git_commit:
            lines.append(f"*Last indexed at commit: `{rm.incremental_state.git_commit}`*")
        lines.append("")

        return "\n".join(lines)

    def _render_directory_tree(
        self,
        node: DirectoryRollup,
        lines: list[str],
        depth: int,
        max_depth: int,
        prefix: str = "",
    ) -> None:
        """Recursively render directory tree."""
        if depth > max_depth:
            return

        # Format: name (files, LOC, language)
        lang_str = f", {node.primary_language.value}" if node.primary_language else ""
        test_str = " [test]" if node.is_test_directory else ""
        gen_str = " [generated]" if node.is_generated else ""

        display = (
            f"{prefix}{node.name}/ "
            f"({node.recursive_file_count} files, {node.recursive_loc:,} LOC{lang_str})"
            f"{test_str}{gen_str}"
        )
        lines.append(display)

        # Render subdirectories
        for i, subdir in enumerate(node.subdirectories[:10]):
            is_last = i == len(node.subdirectories[:10]) - 1
            new_prefix = prefix + ("└── " if is_last else "├── ")
            child_prefix = prefix + ("    " if is_last else "│   ")

            self._render_directory_tree(
                subdir, lines, depth + 1, max_depth, new_prefix if depth > 0 else ""
            )


# =============================================================================
# Convenience Functions
# =============================================================================


def generate_repository_map(
    root: Path,
    cache_dir: Path | None = None,
    output_dir: Path | None = None,
) -> RepositoryMap:
    """Convenience function to generate and optionally export a repository map.

    Args:
        root: Repository root directory
        cache_dir: Optional cache directory for tag extraction
        output_dir: Optional directory to export artifacts

    Returns:
        Complete RepositoryMap
    """
    generator = RepositoryMapGenerator(root=root, cache_dir=cache_dir)
    repo_map = generator.generate()

    if output_dir:
        generator.export_artifacts(output_dir)

    return repo_map


def get_changed_files_from_git(
    root: Path, since_commit: str | None = None
) -> tuple[set[str], set[str], set[str]]:
    """Get changed files from git.

    Args:
        root: Repository root
        since_commit: Commit to compare against (default: HEAD~1)

    Returns:
        Tuple of (modified, added, deleted) file paths
    """
    since = since_commit or "HEAD~1"

    try:
        # Get diff
        result = subprocess.run(
            ["git", "diff", "--name-status", since],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return set(), set(), set()

        modified = set()
        added = set()
        deleted = set()

        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            status, path = parts
            match status[0]:
                case "M":
                    modified.add(path)
                case "A":
                    added.add(path)
                case "D":
                    deleted.add(path)
                case "R":
                    # Renamed: treat as delete + add
                    if "\t" in path:
                        old, new = path.split("\t", 1)
                        deleted.add(old)
                        added.add(new)
                    else:
                        modified.add(path)

        return modified, added, deleted

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return set(), set(), set()
