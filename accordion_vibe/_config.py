"""Activation and path resolution for the Accordion bridge.

The bridge is inert unless an Accordion checkout is configured, either through
the ``ACCORDION_REPO`` environment variable or the ``[accordion] repo`` section
of vibe's config. Nothing here imports the sidecar or spawns anything.

``ACCORDION_REPO``, **not** ``ACCORDION_HOME``: the latter already belongs to
the Accordion extension itself, where it relocates the ``~/.accordion/`` state
directory (registry, door secret, controller lease). Reusing that name would
have made the sidecar write its state into the checkout being pointed at.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema

__all__ = [
    "ACCORDION_APP_ENV",
    "ACCORDION_REPO_ENV",
    "child_environment",
    "resolve_accordion_app",
    "resolve_accordion_repo",
    "sidecar_command",
    "sidecar_log_path",
]

ACCORDION_REPO_ENV = "ACCORDION_REPO"
ACCORDION_APP_ENV = "ACCORDION_APP"

# Accordion's own state-directory override. Never inherited by the sidecar: a
# user who exported it while following older instructions would otherwise make
# the sidecar keep its registry inside whatever directory it names.
_ACCORDION_STATE_HOME_ENV = "ACCORDION_HOME"

# The sidecar bundle sits next to accordion.js so the extension can resolve the
# app binary, the client UI, the skills and the conductors relative to its own
# location. ``extension/dist/sidecar.mjs`` is accepted as a fallback for local
# builds that still emit into dist/.
_SIDECAR_CANDIDATES = ("extension/sidecar.mjs", "extension/dist/sidecar.mjs")


def _configured(config: VibeConfigSchema | None, field: str) -> str:
    if config is None:
        return ""
    section = getattr(config, "accordion", None)
    return str(getattr(section, field, "") or "")


def resolve_accordion_app(config: VibeConfigSchema | None = None) -> str | None:
    """Path to the Accordion desktop binary, forwarded as the app's flag."""
    raw = os.environ.get(ACCORDION_APP_ENV, "").strip() or _configured(config, "app")
    return raw.strip() or None


def resolve_accordion_repo(config: VibeConfigSchema | None = None) -> Path | None:
    """Return the Accordion checkout to bridge to, or None when inactive.

    The environment variable wins over config so a single session can opt in
    without editing any file. A configured-but-missing directory resolves to
    None: a broken path must never turn into a spawn attempt.
    """
    raw = os.environ.get(ACCORDION_REPO_ENV, "").strip() or _configured(config, "repo")
    if not raw.strip():
        return None
    repo = Path(raw.strip()).expanduser()
    return repo if repo.is_dir() else None


def sidecar_command(repo: Path) -> list[str] | None:
    """Return the argv that starts the sidecar, or None when it is missing."""
    for relative in _SIDECAR_CANDIDATES:
        candidate = repo / relative
        if candidate.is_file():
            return ["node", str(candidate)]
    return None


def child_environment() -> dict[str, str]:
    """The environment the sidecar is spawned with.

    A copy of this process's, minus ``ACCORDION_HOME`` — see the module
    docstring. Popen replaces rather than merges, so this returns the whole
    environment, not an overlay.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key != _ACCORDION_STATE_HOME_ENV
    }


def sidecar_log_path(session_id: str) -> Path:
    """Return the file the sidecar's stderr is appended to."""
    directory = Path.home() / ".accordion" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return directory / f"vibe-sidecar-{safe}.log"
