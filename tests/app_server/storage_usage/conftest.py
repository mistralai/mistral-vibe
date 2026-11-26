from __future__ import annotations

from pathlib import Path

import pytest

from tests.app_server.storage_usage._tree import Tree


@pytest.fixture
def tree(config_dir: Path) -> Tree:
    """``VIBE_HOME`` is ``config_dir``: claims resolve there."""
    return Tree(config_dir)
