"""A plugin's Views, which Vibe Code does not run yet (DSK-2078).

A View is an HTML editor a plugin ships under ``ai.mistral.vibe/views``, and
the ``render_<view>`` tool that shows it. Vibe Work runs them; here they are
only read, so the Plugins page and the model can say that they are not
supported (``unsupported.py``) rather than call a tool that does not exist.
"""

from __future__ import annotations

import json
from pathlib import Path

VIEWS_DIR = Path("ai.mistral.vibe") / "views"


def plugin_views(root: Path) -> list[tuple[str, str | None]]:
    """Each View's name and description, as its ``view.json`` gives them."""
    try:
        folders = sorted(path for path in (root / VIEWS_DIR).iterdir() if path.is_dir())
    except OSError:
        return []
    views: list[tuple[str, str | None]] = []
    for folder in folders:
        try:
            document = json.loads((folder / "view.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        name = document.get("name")
        description = document.get("description")
        views.append((
            name if isinstance(name, str) and name else folder.name,
            description if isinstance(description, str) else None,
        ))
    return views
