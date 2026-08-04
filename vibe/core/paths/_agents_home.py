from __future__ import annotations

from pathlib import Path

from vibe.core.paths._vibe_home import GlobalPath

_DEFAULT_AGENTS_HOME = Path.home() / ".agents"


def _get_agents_home() -> Path:
    return _DEFAULT_AGENTS_HOME


AGENTS_HOME = GlobalPath(_get_agents_home)
