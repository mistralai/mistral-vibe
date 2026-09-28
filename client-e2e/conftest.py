"""Keep this suite out of the outer Vibe pytest run."""

from __future__ import annotations

from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    # The root Vibe run collects from vibe/, recursing here where the tests need
    # this folder's pytest.ini, the `e2e` package on the path, and pyte. Skip it
    # unless pytest was pointed at this folder's own config (rootdir == here).
    # Return None (not False) otherwise: this is a firstresult hook, so False
    # would pre-empt the builtin that applies norecursedirs (__pycache__, etc.).
    if config.rootpath != _HERE:
        return True
    return None
