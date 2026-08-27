from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import textwrap

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_script(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


suggest = _load_script("_suggest_lazy_imports_under_test", "suggest_lazy_imports.py")
_lazy_findings = suggest._lazy_findings


def _write(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(source))
    return p


def test_single_function_import_is_suggested(tmp_path: Path):
    f = _write(
        tmp_path,
        "lazy.py",
        """
        from __future__ import annotations
        import heavy

        def f() -> None:
            return heavy.compute()
    """,
    )
    findings = _lazy_findings(f)
    assert len(findings) == 1
    assert findings[0].rule == "lazy"
    assert findings[0].message.startswith("'heavy'")


def test_decorator_use_not_suggested_as_lazy(tmp_path: Path):
    f = _write(
        tmp_path,
        "decorator.py",
        """
        from __future__ import annotations
        import deco

        @deco
        def f() -> None:
            pass
    """,
    )
    assert _lazy_findings(f) == []


def test_default_arg_use_not_suggested_as_lazy(tmp_path: Path):
    f = _write(
        tmp_path,
        "default_arg.py",
        """
        from __future__ import annotations
        import defaultval

        def f(x: int = defaultval) -> None:
            pass
    """,
    )
    assert _lazy_findings(f) == []


def test_annotation_only_import_not_suggested(tmp_path: Path):
    # ``heavy`` appears only in an annotation; under
    # ``from __future__ import annotations`` that reference never runs, so the
    # import has no runtime use and must not be reported as a lazy candidate.
    f = _write(
        tmp_path,
        "annot_only.py",
        """
        from __future__ import annotations
        import heavy

        def f() -> heavy.Thing:
            return None
    """,
    )
    assert _lazy_findings(f) == []


def test_multi_function_use_not_suggested(tmp_path: Path):
    # Used in two functions -> cannot move into a single function body.
    f = _write(
        tmp_path,
        "multi.py",
        """
        from __future__ import annotations
        import heavy

        def f() -> None:
            heavy.a()

        def g() -> None:
            heavy.b()
    """,
    )
    assert _lazy_findings(f) == []


def test_type_checking_else_branch_use_not_suggested(tmp_path: Path):
    # The ``else`` branch of ``if TYPE_CHECKING:`` runs at import time, so a
    # name referenced there is a module-level runtime use and must not be
    # reported as a lazy candidate (regression for the whole-if skip).
    f = _write(
        tmp_path,
        "tc_else.py",
        """
        from __future__ import annotations
        from typing import TYPE_CHECKING
        import heavy

        if TYPE_CHECKING:
            x: int = 1
        else:
            _y = heavy
    """,
    )
    assert _lazy_findings(f) == []
