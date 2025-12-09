from __future__ import annotations

from pathlib import Path

from textual.widgets import Static


class PathDisplay(Static):
    def __init__(self, path: Path | str) -> None:
        super().__init__()
        self.can_focus = False
        self._path = Path(path)
        self._update_display()

    def _update_display(self) -> None:
        path_str = str(self._path)
        try:
            home = Path.home()
            if self._path.is_relative_to(home):
                path_str = f"~/{self._path.relative_to(home)}"
        except (ValueError, OSError):
            pass

        self.update(path_str)

    def set_path(self, path: Path | str) -> None:
        self._path = Path(path)
        self._update_display()
