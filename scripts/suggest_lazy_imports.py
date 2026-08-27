#!/usr/bin/env python3
"""Suggest module-level imports that could be deferred to cut startup cost.

Two categories of suggestions are reported:

1. **type-checking** -- a top-level import used only in annotations. Ruff's
   TC001/TC002/TC003 rules detect these; this script shells out to ruff
   rather than duplicating the analysis.
2. **lazy** -- a top-level import whose every runtime reference is confined
   to a single function. No Ruff rule covers this; the AST-based analysis
   below handles it.

The lazy analysis is a heuristic based on ``ast`` name references. It does
not execute code and cannot see dynamic references (``getattr``,
``importlib``, re-exports via ``__all__``, plugin registries, side-effect
imports). Treat every suggestion as a lead to verify, not an autofix.
``__init__.py`` files are skipped because their imports are frequently
re-exports.

Usage::

    uv run scripts/suggest_lazy_imports.py [path ...]

Defaults to scanning ``vibe/``. Exits 0 (informational); pass ``--check``
to exit non-zero when any suggestion is found (for CI gates).

Display modes (default is flat listing)::

    --stats           per-rule counts
    --tree            tree grouped by directory
    --depth N         tree depth (default 1, only with --tree)
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_ROOT = REPO_ROOT / "vibe"
TC_SUGGEST_RULES = ["TC001", "TC002", "TC003"]


@dataclass(frozen=True)
class Finding:
    file: Path
    lineno: int
    col: int
    rule: str  # "TC001", "TC002", "TC003", "lazy"
    message: str


# ---------------------------------------------------------------------------
# Lazy-import AST analysis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportSite:
    name: str
    lineno: int
    col: int
    kind: str  # "import" or "from"


def _type_checking_if(node: ast.AST) -> ast.If | None:
    if not isinstance(node, ast.If):
        return None
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return node
    if (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    ):
        return node
    return None


def _annotation_node_roots(module: ast.Module) -> list[ast.AST]:
    roots: list[ast.AST] = []
    for parent in ast.walk(module):
        if isinstance(parent, ast.AnnAssign) and parent.annotation is not None:
            roots.append(parent.annotation)
        elif isinstance(parent, ast.arg) and parent.annotation is not None:
            roots.append(parent.annotation)
        elif (
            isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef)
            and parent.returns is not None
        ):
            roots.append(parent.returns)
        elif isinstance(parent, ast.TypeAlias):
            roots.append(parent.value)
        for param in getattr(parent, "type_params", ()) or ():
            if (bound := getattr(param, "bound", None)) is not None:
                roots.append(bound)
            if (default := getattr(param, "default_value", None)) is not None:
                roots.append(default)
    return roots


def _name_positions_in(node: ast.AST) -> set[tuple[int, int]]:
    return {
        (child.lineno, child.col_offset)
        for child in ast.walk(node)
        if isinstance(child, ast.Name)
    }


def _enclosing_function(
    module: ast.Module, target: ast.Name
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    innermost = None
    for parent in ast.walk(module):
        if not isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for stmt in parent.body:
            for child in ast.walk(stmt):
                if child is target:
                    innermost = parent
                    break
            else:
                continue
            break
    return innermost


def _import_bound_names(stmt: ast.Import | ast.ImportFrom) -> Iterator[tuple[str, str]]:
    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            yield (alias.asname or alias.name.split(".")[0], "import")
    else:
        for alias in stmt.names:
            if alias.name == "*":
                continue
            yield (alias.asname or alias.name, "from")


def _module_level_imports(module: ast.Module) -> dict[str, ImportSite]:
    sites: dict[str, ImportSite] = {}
    for stmt in module.body:
        if _type_checking_if(stmt) is not None:
            continue
        if not isinstance(stmt, ast.Import | ast.ImportFrom):
            continue
        for name, kind in _import_bound_names(stmt):
            sites[name] = ImportSite(name, stmt.lineno, stmt.col_offset, kind)
    return sites


def _runtime_name_nodes(stmt: ast.stmt) -> Iterator[ast.AST]:
    # The body of ``if TYPE_CHECKING:`` never runs, so its names are excluded.
    # The test and any ``else``/``elif`` branches DO run at import time, so they
    # are walked -- skipping the whole node would lose those references and
    # could surface them as lazy-move candidates, breaking modules on import.
    if (tc_if := _type_checking_if(stmt)) is not None:
        yield from ast.walk(tc_if.test)
        for else_stmt in tc_if.orelse:
            yield from ast.walk(else_stmt)
        return
    yield from ast.walk(stmt)


def _names_used_outside_functions(module: ast.Module) -> set[tuple[int, int]]:
    in_function: set[int] = set()
    for parent in ast.walk(module):
        if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef):
            for stmt in parent.body:
                for child in ast.walk(stmt):
                    in_function.add(id(child))

    positions: set[tuple[int, int]] = set()
    for stmt in module.body:
        for child in _runtime_name_nodes(stmt):
            if not isinstance(child, ast.Name):
                continue
            if id(child) in in_function:
                continue
            positions.add((child.lineno, child.col_offset))
    return positions


def _lazy_findings(path: Path) -> list[Finding]:
    if path.name == "__init__.py":
        return []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    imports = _module_level_imports(tree)
    if not imports:
        return []

    annotation_pos: set[tuple[int, int]] = set()
    for root in _annotation_node_roots(tree):
        annotation_pos |= _name_positions_in(root)

    module_level_runtime = _names_used_outside_functions(tree)

    refs_by_name: dict[str, list[ast.Name]] = {}
    annot_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name):
            continue
        if node.id not in imports:
            continue
        if (node.lineno, node.col_offset) in annotation_pos:
            annot_names.add(node.id)
        else:
            refs_by_name.setdefault(node.id, []).append(node)

    findings: list[Finding] = []
    for name, site in imports.items():
        rts = refs_by_name.get(name, [])
        if not rts:
            continue
        if name in annot_names:
            continue
        if any((r.lineno, r.col_offset) in module_level_runtime for r in rts):
            continue

        enclosing_funcs = {
            id(func)
            for ref in rts
            if (func := _enclosing_function(tree, ref)) is not None
        }
        if len(enclosing_funcs) == 1:
            func = _enclosing_function(tree, rts[0])
            func_name = getattr(func, "name", "<unknown>") if func else "<unknown>"
            findings.append(
                Finding(
                    file=path,
                    lineno=site.lineno,
                    col=site.col,
                    rule="lazy",
                    message=(
                        f"'{name}' is only used inside '{func_name}()'; "
                        "move the import into the function body to defer it"
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Ruff TC001-003 collection
# ---------------------------------------------------------------------------


RUFF_TIMEOUT = 120  # seconds; ruff on vibe/ finishes in well under this


def _ruff_tc_findings(roots: list[Path]) -> list[Finding]:
    cmd = [
        "uv",
        "run",
        "ruff",
        "check",
        "--select",
        ",".join(TC_SUGGEST_RULES),
        "--no-fix",
        "--output-format",
        "json",
        *[str(r) for r in roots],
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=RUFF_TIMEOUT
        )
    except subprocess.SubprocessError as e:
        raise RuntimeError(f"ruff invocation failed: {e}") from e
    # ruff exits 1 when TC001-003 findings exist; anything else is a tool error.
    # Surface it loudly so --check never masquerades a ruff crash as a clean pass.
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"ruff exited with code {result.returncode}:\n{result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"ruff returned non-JSON output:\n{result.stdout[:500]}"
        ) from e
    findings: list[Finding] = []
    for item in data:
        # Ruff always emits syntax errors with ``code: null`` regardless of
        # --select; only accept the TC rules we asked for so they neither pollute
        # --stats (``sorted`` mixes ``None`` with ``str``) nor false-trip --check.
        code = item.get("code")
        if code not in TC_SUGGEST_RULES:
            continue
        loc = item.get("location", {})
        findings.append(
            Finding(
                file=Path(item["filename"]),
                lineno=loc.get("row", 0),
                col=loc.get("column", 0),
                rule=code,
                message=item["message"],
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------


def _iter_python_files(roots: list[Path]) -> Iterator[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def _collect_all(roots: list[Path]) -> list[Finding]:
    findings = _ruff_tc_findings(roots)
    for path in _iter_python_files(roots):
        findings.extend(_lazy_findings(path))
    return findings


def _rel(path: Path) -> Path:
    return path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_flat(findings: list[Finding]) -> None:
    for f in findings:
        print(f"{_rel(f.file)}:{f.lineno}:{f.col}: {f.message} [{f.rule}]")


def _render_stats(findings: list[Finding]) -> None:
    counts = Counter(f.rule for f in findings)
    if not counts:
        print("No suggestions found.")
        return
    width = max(len(r) for r in counts)
    for rule in sorted(counts):
        print(f"  {rule:<{width}}  {counts[rule]}")


def _render_tree(findings: list[Finding], depth: int) -> None:
    # Group counts by directory prefix up to `depth` levels.
    bucket: dict[tuple[str, ...], int] = defaultdict(int)
    for f in findings:
        parts = _rel(f.file).parts
        dirs = parts[:-1] if len(parts) > 1 else ()
        prefix = dirs[:depth] if dirs else ()
        bucket[prefix] += 1

    total = sum(bucket.values())
    print(f". ({total})")

    segments = _ordered_segments(bucket, ())
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        count = sum(v for k, v in bucket.items() if k and k[0] == seg)
        branch = "└── " if last else "├── "
        print(f"{branch}{seg}/ ({count})")
        if depth > 1:
            _render_subtree(bucket, (seg,), depth, 2, "    " if last else "│   ")


def _ordered_segments(
    bucket: dict[tuple[str, ...], int], parent: tuple[str, ...]
) -> list[str]:
    segments: list[str] = []
    seen: set[str] = set()
    for key in bucket:
        if len(key) <= len(parent):
            continue
        if key[: len(parent)] != parent:
            continue
        seg = key[len(parent)]
        if seg not in seen:
            segments.append(seg)
            seen.add(seg)
    return segments


def _render_subtree(
    bucket: dict[tuple[str, ...], int],
    parent: tuple[str, ...],
    depth: int,
    level: int,
    indent: str,
) -> None:
    segments = _ordered_segments(bucket, parent)
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        full = (*parent, seg)
        count = sum(v for k, v in bucket.items() if k[:level] == full)
        branch = "└── " if last else "├── "
        print(f"{indent}{branch}{seg}/ ({count})")
        if level < depth:
            _render_subtree(
                bucket, full, depth, level + 1, indent + ("    " if last else "│   ")
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    description = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "paths", nargs="*", help="Files or directories to scan (default: vibe/)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when any suggestion is found (CI gate).",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print per-rule counts instead of the flat listing.",
    )
    parser.add_argument(
        "--tree",
        action="store_true",
        help="Print a tree grouped by directory instead of the flat listing.",
    )
    parser.add_argument(
        "--depth", type=int, default=1, help="Tree depth (only with --tree, default 1)."
    )
    args = parser.parse_args(argv[1:])

    roots = [Path(a).resolve() for a in args.paths] or [DEFAULT_SCAN_ROOT]
    findings = _collect_all(roots)

    if args.stats:
        _render_stats(findings)
    elif args.tree:
        _render_tree(findings, args.depth)
    else:
        _render_flat(findings)

    total = len(findings)
    if total:
        print(f"\n{total} suggestion(s) found.")
        if args.check:
            return 1
    else:
        print("No deferred-import suggestions found.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
