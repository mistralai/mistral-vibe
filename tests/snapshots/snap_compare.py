from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from pathlib import PurePath
from typing import Protocol

from textual.app import App
from textual.pilot import Pilot


class SnapCompare(Protocol):
    def __call__(
        self,
        app: str | PurePath | App,
        /,
        *,
        press: Iterable[str] = ...,
        terminal_size: tuple[int, int] = ...,
        run_before: (Callable[[Pilot], Awaitable[None] | None] | None) = ...,
    ) -> bool: ...
