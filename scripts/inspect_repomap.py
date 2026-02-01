from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from vibe.core.paths.global_paths import VIBE_HOME
from vibe.repomap.core import RepoMap, extract_mentions_from_text
from vibe.repomap.rendering import render_repo_map_markdown


def inspect(query: str, *, verbose: bool = False, markdown_output: Path | None = None) -> None:
    cache_dir = VIBE_HOME.path / "cache" / "repomap"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"Initializing RepoMap (Cache: {cache_dir})...")
    repo_map = RepoMap(
        root=str(Path.cwd()),
        verbose=verbose,
        cache_dir=str(cache_dir),
    )

    mentioned_idents = extract_mentions_from_text(query)

    print(f"Query: '{query}'")
    print(f"Extracted Identifiers: {mentioned_idents}")

    print("Scanning files...", end="", flush=True)
    cwd = Path.cwd()
    other_files: list[str] = []
    supported_extensions = {".py", ".go", ".js", ".ts", ".jsx", ".tsx"}

    for fpath in cwd.rglob("*"):
        if not fpath.is_file():
            continue
        # Skip dotfiles and common exclusions
        if any(part.startswith(".") for part in fpath.parts):
            continue
        if any(
            part in {"node_modules", "venv", "env", ".venv", "test_env", "__pycache__", "site-packages", "dist", "build"}
            for part in fpath.parts
        ):
            continue
        if fpath.suffix in supported_extensions:
            other_files.append(str(fpath))

    print(f" Done ({len(other_files)} files).")

    print("Generating RepoMap...")
    result = repo_map.get_repo_map_with_diagnostics(
        chat_files=[],
        other_files=other_files,
        mentioned_fnames=set(),
        mentioned_idents=mentioned_idents,
    )

    print("\n" + "=" * 40 + "\nRepoMap Content:\n" + "=" * 40)
    print(result.content)
    print("=" * 40)
    print(f"Total Tokens (approx): {len(result.content) // 4}")

    if markdown_output:
        markdown = render_repo_map_markdown(
            result.ranked_defs,
            result.definitions,
            repo_map.map_tokens,
            set(),
            include_all=True,
        )
        markdown_output.write_text(markdown, encoding="utf-8")
        print(f"Markdown written to: {markdown_output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect RepoMap generation for a query.")
    parser.add_argument("query", help="The simulated user query string")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Write a Markdown repo map to this path",
    )

    args = parser.parse_args()
    inspect(args.query, verbose=args.verbose, markdown_output=args.markdown)
