"""
Aider's RepoMap implementation for Mistral Vibe.

This module provides comprehensive repository mapping with:
- Hierarchical directory summaries with rollups
- Symbol indexing with relationships
- Dependency graph generation (JSON/GraphML/GEXF)
- Entrypoint detection (CLI, API, services)
- Core module identification
- Incremental indexing via git-diff + file hashes
"""
from .core import RepoMap, RepoMapResult, extract_mentions_from_text
from .discovery import DiscoveryResult, discover_files, SUPPORTED_EXTENSIONS
from .tags import ExtractionError, ExtractionResult, Tag, TagExtractor

# New comprehensive repository map modules
from .analyzer import DirectoryAnalyzer, analyze_directory
from .exporter import (
    export_json,
    export_graphml,
    export_gexf,
    export_all_formats,
    build_networkx_graph,
    from_networkx_graph,
    compute_graph_statistics,
)
from .generator import (
    RepositoryMapGenerator,
    generate_repository_map,
    get_changed_files_from_git,
)
from .schemas import (
    # Enums
    FileType,
    Language,
    SymbolKind,
    RelationshipKind,
    EntrypointType,
    # File-level
    FileMetrics,
    FileHash,
    # Directory-level
    DirectoryRollup,
    # Symbol-level
    Symbol,
    SymbolLocation,
    SymbolRelationship,
    FileSymbolIndex,
    # Graph
    GraphNode,
    GraphEdge,
    DependencyGraph,
    # Entrypoints and core
    Entrypoint,
    CoreModule,
    # Incremental
    IncrementalState,
    # Complete map
    RepositoryMap,
    RepositoryMapSummary,
)

__all__ = [
    # Existing exports
    "RepoMap",
    "RepoMapResult",
    "Tag",
    "TagExtractor",
    "ExtractionError",
    "ExtractionResult",
    "DiscoveryResult",
    "discover_files",
    "SUPPORTED_EXTENSIONS",
    "extract_mentions_from_text",
    # Analyzer
    "DirectoryAnalyzer",
    "analyze_directory",
    # Exporter
    "export_json",
    "export_graphml",
    "export_gexf",
    "export_all_formats",
    "build_networkx_graph",
    "from_networkx_graph",
    "compute_graph_statistics",
    # Generator
    "RepositoryMapGenerator",
    "generate_repository_map",
    "get_changed_files_from_git",
    # Schemas - Enums
    "FileType",
    "Language",
    "SymbolKind",
    "RelationshipKind",
    "EntrypointType",
    # Schemas - File
    "FileMetrics",
    "FileHash",
    # Schemas - Directory
    "DirectoryRollup",
    # Schemas - Symbol
    "Symbol",
    "SymbolLocation",
    "SymbolRelationship",
    "FileSymbolIndex",
    # Schemas - Graph
    "GraphNode",
    "GraphEdge",
    "DependencyGraph",
    # Schemas - Entrypoints
    "Entrypoint",
    "CoreModule",
    # Schemas - Incremental
    "IncrementalState",
    # Schemas - Complete
    "RepositoryMap",
    "RepositoryMapSummary",
]
