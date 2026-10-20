"""Tracks the newest registry version we have already told the user about.

This lives in the (non-committed) global cache, not in the manifests, so that
committed ``.vibe/skills.toml`` files stay clean. It lets the session-start CTA
fire only when a version is genuinely *new since last session*, rather than every
time a frozen pin lags behind the registry.
"""

from __future__ import annotations

import json
from pathlib import Path

from vibe.core.config.harness_files._paths import GLOBAL_REGISTRY_SKILLS_CACHE_DIR
from vibe.observability.logging import logger
from vibe.utils.io import read_safe


def _state_path() -> Path:
    return GLOBAL_REGISTRY_SKILLS_CACHE_DIR.path / "seen-versions.json"


def load_seen() -> dict[str, int]:
    try:
        data = json.loads(read_safe(_state_path()).text)
    except (FileNotFoundError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, int)}


def mark_seen(updates: dict[str, int]) -> None:
    """Merge ``{skill_id: latest_version}`` into the persisted state (max wins)."""
    if not updates:
        return
    current = load_seen()
    changed = False
    for skill_id, version in updates.items():
        if version > current.get(skill_id, 0):
            current[skill_id] = version
            changed = True
    if not changed:
        return
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to persist skill notify state: %s", exc)
