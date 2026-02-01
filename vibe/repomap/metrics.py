"""Observability infrastructure for RepoMap."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PhaseMetrics:
    """Metrics for a single pipeline phase."""

    name: str
    duration_ms: float = 0.0
    items_processed: int = 0
    items_skipped: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepoMapMetrics:
    """Comprehensive metrics for a RepoMap run."""

    # Phase timings
    phases: list[PhaseMetrics] = field(default_factory=list)

    # File statistics
    files_scanned: int = 0
    files_processed: int = 0
    files_skipped: int = 0

    # Tag statistics
    tags_extracted: int = 0
    definitions_count: int = 0
    references_count: int = 0

    # Cache statistics
    cache_hits: int = 0
    cache_misses: int = 0

    # Graph statistics
    graph_nodes: int = 0
    graph_edges: int = 0

    # Rendering statistics
    tokens_budget: int = 0
    tokens_used: int = 0
    symbols_rendered: int = 0

    # Total time
    total_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_duration_ms": self.total_duration_ms,
            "files": {
                "scanned": self.files_scanned,
                "processed": self.files_processed,
                "skipped": self.files_skipped,
            },
            "tags": {
                "total": self.tags_extracted,
                "definitions": self.definitions_count,
                "references": self.references_count,
            },
            "cache": {
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "hit_rate": (
                    self.cache_hits / (self.cache_hits + self.cache_misses)
                    if (self.cache_hits + self.cache_misses) > 0
                    else 0.0
                ),
            },
            "graph": {
                "nodes": self.graph_nodes,
                "edges": self.graph_edges,
            },
            "rendering": {
                "tokens_budget": self.tokens_budget,
                "tokens_used": self.tokens_used,
                "utilization": (
                    self.tokens_used / self.tokens_budget
                    if self.tokens_budget > 0
                    else 0.0
                ),
                "symbols_rendered": self.symbols_rendered,
            },
            "phases": [
                {
                    "name": p.name,
                    "duration_ms": p.duration_ms,
                    "items_processed": p.items_processed,
                    "items_skipped": p.items_skipped,
                    **p.extra,
                }
                for p in self.phases
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def summary(self) -> str:
        """Generate a human-readable summary."""
        lines = [
            f"RepoMap completed in {self.total_duration_ms:.1f}ms",
            f"  Files: {self.files_processed}/{self.files_scanned} processed ({self.files_skipped} skipped)",
            f"  Tags: {self.tags_extracted} ({self.definitions_count} defs, {self.references_count} refs)",
            f"  Cache: {self.cache_hits} hits, {self.cache_misses} misses",
            f"  Graph: {self.graph_nodes} nodes, {self.graph_edges} edges",
            f"  Output: {self.tokens_used}/{self.tokens_budget} tokens ({self.symbols_rendered} symbols)",
        ]
        return "\n".join(lines)


class PhaseTimer:
    """Context manager for timing pipeline phases."""

    def __init__(self, name: str, metrics: RepoMapMetrics):
        self.name = name
        self.metrics = metrics
        self.start_time: float = 0.0
        self.phase = PhaseMetrics(name=name)

    def __enter__(self) -> PhaseMetrics:
        self.start_time = time.perf_counter()
        return self.phase

    def __exit__(self, *args):
        self.phase.duration_ms = (time.perf_counter() - self.start_time) * 1000
        self.metrics.phases.append(self.phase)


@dataclass
class DebugDump:
    """Debug information dump for troubleshooting."""

    # Top ranked definitions with scores
    top_definitions: list[tuple[str, str, float]] = field(default_factory=list)

    # Personalization vector (top entries)
    personalization_top: list[tuple[str, float]] = field(default_factory=list)

    # Mention matches
    mention_matches: dict[str, list[str]] = field(default_factory=dict)

    # Errors encountered
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "top_definitions": [
                {"file": f, "symbol": s, "score": sc}
                for f, s, sc in self.top_definitions
            ],
            "personalization_top": [
                {"file": f, "score": s} for f, s in self.personalization_top
            ],
            "mention_matches": self.mention_matches,
            "errors": self.errors,
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: str | Path) -> None:
        """Save debug dump to file."""
        Path(path).write_text(self.to_json())
