#!/usr/bin/env python3
"""CLI script to generate comprehensive repository maps.

Usage:
    uv run scripts/generate_repomap.py [OPTIONS]

Examples:
    # Generate and export all formats
    uv run scripts/generate_repomap.py --output ./artifacts

    # Generate JSON only
    uv run scripts/generate_repomap.py --output ./artifacts --formats json

    # Print Markdown to stdout
    uv run scripts/generate_repomap.py --markdown

    # Incremental update from last commit
    uv run scripts/generate_repomap.py --incremental --output ./artifacts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path.cwd()))

from vibe.core.paths.global_paths import VIBE_HOME
from vibe.repomap.generator import (
    RepositoryMapGenerator,
    generate_repository_map,
    get_changed_files_from_git,
)
from vibe.repomap.schemas import IncrementalState


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate comprehensive repository maps with rollups, symbols, and dependency graphs."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root directory (default: current directory)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory for artifacts",
    )
    parser.add_argument(
        "--formats",
        type=str,
        default="json,graphml,gexf,markdown",
        help="Comma-separated list of formats to export (json,graphml,gexf,markdown)",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Print Markdown summary to stdout",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON to stdout",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Use incremental indexing (requires previous state)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="Path to previous state file for incremental updates",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print statistics about the repository",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # Setup cache directory
    cache_dir = VIBE_HOME.path / "cache" / "repomap"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if args.verbose:
        print(f"Repository root: {args.root}")
        print(f"Cache directory: {cache_dir}")

    # Load previous state if incremental
    previous_state = None
    if args.incremental and args.state_file:
        try:
            state_data = json.loads(args.state_file.read_text())
            previous_state = IncrementalState.model_validate(state_data)
            if args.verbose:
                print(f"Loaded previous state from {args.state_file}")
                print(f"  Last indexed: {previous_state.last_indexed}")
                print(f"  Git commit: {previous_state.git_commit}")
                print(f"  Files: {len(previous_state.indexed_files)}")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Could not load state file: {e}", file=sys.stderr)

    # Show git changes if incremental
    if args.incremental and args.verbose:
        modified, added, deleted = get_changed_files_from_git(args.root)
        print(f"\nGit changes since last commit:")
        print(f"  Modified: {len(modified)}")
        print(f"  Added: {len(added)}")
        print(f"  Deleted: {len(deleted)}")

    # Generate repository map
    if args.verbose:
        print("\nGenerating repository map...")

    generator = RepositoryMapGenerator(
        root=args.root,
        cache_dir=cache_dir,
    )

    repo_map = generator.generate(
        incremental=args.incremental,
        previous_state=previous_state,
    )

    # Print statistics
    if args.stats or args.verbose:
        print("\n" + "=" * 50)
        print("Repository Statistics")
        print("=" * 50)
        print(f"  Name: {repo_map.summary.name}")
        print(f"  Total Files: {repo_map.summary.total_files}")
        print(f"  Total LOC: {repo_map.summary.total_loc:,}")
        print(f"  Primary Language: {repo_map.summary.primary_language}")
        print(f"  Entrypoints: {len(repo_map.entrypoints)}")
        print(f"  Core Modules: {len(repo_map.core_modules)}")
        print(f"  Symbol Indices: {len(repo_map.symbol_index)}")
        print(f"  Graph Nodes: {repo_map.dependency_graph.node_count}")
        print(f"  Graph Edges: {repo_map.dependency_graph.edge_count}")

        if repo_map.summary.language_stats:
            print("\n  Language Breakdown:")
            total = sum(repo_map.summary.language_stats.values())
            for lang, loc in sorted(
                repo_map.summary.language_stats.items(), key=lambda x: -x[1]
            )[:5]:
                pct = (loc / total * 100) if total > 0 else 0
                print(f"    {lang}: {loc:,} LOC ({pct:.1f}%)")

    # Export artifacts
    if args.output:
        formats = set(args.formats.split(","))
        if args.verbose:
            print(f"\nExporting artifacts to {args.output}...")
            print(f"  Formats: {', '.join(formats)}")

        paths = generator.export_artifacts(args.output, formats=formats)

        if args.verbose:
            print("\nExported files:")
            for fmt, path in paths.items():
                size = path.stat().st_size
                print(f"  {fmt}: {path} ({size:,} bytes)")

    # Print to stdout
    if args.markdown:
        print(generator.render_markdown())

    if args.json:
        print(repo_map.model_dump_json(indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
