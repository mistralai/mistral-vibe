"""Runtime import-contract checker.

Verifies that every ``from <module> import <name>`` across the vibe source tree
and tests actually resolves at runtime. Catches re-exports that ruff TC001/2/3
may have silently moved under ``if TYPE_CHECKING:``, breaking
``from pkg.mod import Name`` at runtime even though pyright/TC004 see no problem.

Also force-rebuilds every Pydantic ``BaseModel`` subclass to catch field types
that were moved to ``TYPE_CHECKING`` — these fail lazily (not at import time)
when the model is first validated or serialized.

Usage::

    uv run python scripts/check_import_contracts.py [repo_root]

If ``repo_root`` is omitted, the parent of this script's directory is used.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import sys

_MAX_AFFECTED_REPORT = 20


def _is_vibe_module(module: str) -> bool:
    return module == "vibe" or module.startswith("vibe.")


def _missing_dep_name(exc: Exception) -> str | None:
    """Return the missing module name if *exc* is a non-vibe dependency issue."""
    if isinstance(exc, ModuleNotFoundError) and exc.name is not None:
        return exc.name if not _is_vibe_module(exc.name) else None
    return None


def _error_sig(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


def _resolve_relative(
    file_path: Path, vibe_pkg: Path, level: int, name: str | None
) -> str | None:
    rel = file_path.relative_to(vibe_pkg).with_suffix("")
    if file_path.name == "__init__.py":
        # __init__.py represents the package itself, not a sub-module named __init__.
        pkg_parts = list(rel.parent.parts)
    else:
        pkg_parts = list(rel.parts[:-1])
    if level - 1 > len(pkg_parts):
        return None
    base = pkg_parts[: len(pkg_parts) - (level - 1)] if level > 1 else pkg_parts
    if name:
        base += name.split(".")
    return ".".join(["vibe", *base]) if base else "vibe"


class ImportVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path, vibe_pkg: Path) -> None:
        self.file_path = file_path
        self.vibe_pkg = vibe_pkg
        self.imports: list[tuple[str, str, int]] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            mod = _resolve_relative(
                self.file_path, self.vibe_pkg, node.level, node.module
            )
            if mod is None:
                return
        else:
            mod = node.module or ""
        if not mod:
            return
        for alias in node.names:
            self.imports.append((
                mod,
                "*" if alias.name == "*" else alias.name,
                node.lineno,
            ))


def collect_imports(
    repo: Path, vibe_pkg: Path
) -> dict[Path, list[tuple[str, str, int]]]:
    result: dict[Path, list[tuple[str, str, int]]] = {}
    for root in [vibe_pkg, repo / "tests"]:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except SyntaxError:
                continue
            visitor = ImportVisitor(py, vibe_pkg)
            visitor.visit(tree)
            if visitor.imports:
                result[py] = visitor.imports
    return result


def _check_contracts(
    pairs: dict[tuple[str, str], set[Path]], star_modules: dict[str, set[Path]]
) -> tuple[list[str], dict[str, list[str]], list[str], int]:
    failures: list[str] = []
    by_sig: dict[str, list[str]] = {}
    env_warnings: list[str] = []
    checked = 0

    for (mod, name), _files in sorted(pairs.items()):
        try:
            module = importlib.import_module(mod)
        except Exception as exc:
            checked += 1
            if (dep := _missing_dep_name(exc)) is not None:
                env_warnings.append(f"import {mod} (missing dep: {dep})")
            else:
                by_sig.setdefault(_error_sig(exc), []).append(f"import {mod}")
            continue

        try:
            present = hasattr(module, name)
        except Exception as exc:
            checked += 1
            if (dep := _missing_dep_name(exc)) is not None:
                env_warnings.append(f"from {mod} import {name} (missing dep: {dep})")
            else:
                by_sig.setdefault(_error_sig(exc), []).append(
                    f"from {mod} import {name}"
                )
            continue

        if not present:
            try:
                importlib.import_module(f"{mod}.{name}")
            except ModuleNotFoundError as exc:
                checked += 1
                if (dep := _missing_dep_name(exc)) is not None:
                    env_warnings.append(
                        f"from {mod} import {name} (missing dep: {dep})"
                    )
                else:
                    failures.append(
                        f"ATTR FAIL: `from {mod} import {name}` — "
                        "name not present at runtime"
                    )
                continue
            except ImportError:
                failures.append(
                    f"ATTR FAIL: `from {mod} import {name}` — "
                    "name not present at runtime"
                )
            except Exception as exc:
                checked += 1
                if (dep := _missing_dep_name(exc)) is not None:
                    env_warnings.append(
                        f"from {mod} import {name} (missing dep: {dep})"
                    )
                else:
                    by_sig.setdefault(_error_sig(exc), []).append(
                        f"from {mod} import {name}"
                    )
                continue
        checked += 1

    star_sig, star_env, star_checked = _check_star_modules(star_modules)
    by_sig.update(star_sig)
    env_warnings.extend(star_env)
    checked += star_checked

    return failures, by_sig, env_warnings, checked


def _check_star_modules(
    star_modules: dict[str, set[Path]],
) -> tuple[dict[str, list[str]], list[str], int]:
    by_sig: dict[str, list[str]] = {}
    env_warnings: list[str] = []
    checked = 0
    for mod, _files in sorted(star_modules.items()):
        try:
            importlib.import_module(mod)
        except Exception as exc:
            if (dep := _missing_dep_name(exc)) is not None:
                env_warnings.append(f"import {mod} (*) (missing dep: {dep})")
            else:
                by_sig.setdefault(_error_sig(exc), []).append(f"import {mod} (*)")
        checked += 1
    return by_sig, env_warnings, checked


def _check_pydantic_models() -> tuple[dict[str, list[str]], int]:
    from pydantic import BaseModel

    by_sig: dict[str, list[str]] = {}
    rebuilt = 0
    for mod_name in sorted(sys.modules):
        if not _is_vibe_module(mod_name):
            continue
        mod_obj = sys.modules[mod_name]
        for attr_name in dir(mod_obj):
            try:
                obj = getattr(mod_obj, attr_name)
            except Exception:
                continue
            if not isinstance(obj, type) or not issubclass(obj, BaseModel):
                continue
            if obj is BaseModel:
                continue
            try:
                obj.model_rebuild()
                rebuilt += 1
            except Exception as exc:
                sig = (
                    f"PydanticModelRebuild: {obj.__qualname__}: "
                    f"{str(exc).splitlines()[0]}"
                )
                by_sig.setdefault(sig, []).append(f"{mod_name}.{attr_name}")
                rebuilt += 1
    return by_sig, rebuilt


def _print_report(
    failures: list[str],
    by_sig: dict[str, list[str]],
    env_warnings: list[str],
    checked: int,
    rebuilt: int,
) -> None:
    print(f"Checked {checked} (module, name) contracts.")
    print(f"Checked {rebuilt} Pydantic models for rebuild completeness.")
    print(f"Distinct attribute failures: {len(failures)}")
    for line in failures:
        print("  " + line)
    print(f"\nDistinct import-time error signatures: {len(by_sig)}")
    for sig, affected in sorted(by_sig.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  [{len(affected)} affected] {sig}")
        unique = sorted(set(affected))
        for a in unique[:_MAX_AFFECTED_REPORT]:
            print(f"      - {a}")
        if len(unique) > _MAX_AFFECTED_REPORT:
            print(f"      ... and {len(unique) - _MAX_AFFECTED_REPORT} more")
    if env_warnings:
        print(f"\nEnvironment warnings (non-blocking): {len(env_warnings)}")
        for w in sorted(set(env_warnings))[:_MAX_AFFECTED_REPORT]:
            print(f"  - {w}")


def check_all() -> int:
    repo = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else Path(__file__).resolve().parent.parent
    )
    vibe_pkg = repo / "vibe"
    sys.path.insert(0, str(repo))

    pairs: dict[tuple[str, str], set[Path]] = {}
    star_modules: dict[str, set[Path]] = {}
    for file_path, imports in collect_imports(repo, vibe_pkg).items():
        for mod, name, _lineno in imports:
            if not _is_vibe_module(mod):
                continue
            if name == "*":
                star_modules.setdefault(mod, set()).add(file_path)
                continue
            pairs.setdefault((mod, name), set()).add(file_path)

    failures, contract_errors, env_warnings, checked = _check_contracts(
        pairs, star_modules
    )
    pydantic_errors, rebuilt = _check_pydantic_models()

    all_errors = {**contract_errors, **pydantic_errors}
    _print_report(failures, all_errors, env_warnings, checked, rebuilt)
    return 1 if (failures or all_errors) else 0


if __name__ == "__main__":
    sys.exit(check_all())
