from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum, auto


class QueuedItemKind(StrEnum):
    PROMPT = auto()
    BASH = auto()


@dataclass(frozen=True, slots=True)
class QueuedItem:
    kind: QueuedItemKind
    content: str


@dataclass(slots=True)
class MessageQueue:
    _items: list[QueuedItem] = field(default_factory=list)
    _paused: bool = False
    _on_change: Callable[[], None] | None = None

    def set_change_listener(self, listener: Callable[[], None] | None) -> None:
        self._on_change = listener

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    @property
    def items(self) -> list[QueuedItem]:
        return list(self._items)

    @property
    def paused(self) -> bool:
        return self._paused

    def append_prompt(self, content: str) -> None:
        self._items.append(QueuedItem(QueuedItemKind.PROMPT, content))
        self._notify()

    def append_bash(self, content: str) -> None:
        self._items.append(QueuedItem(QueuedItemKind.BASH, content))
        self._notify()

    def pop_last(self) -> QueuedItem | None:
        if not self._items:
            return None
        item = self._items.pop()
        self._notify()
        return item

    def pop_first(self) -> QueuedItem | None:
        if not self._items:
            return None
        item = self._items.pop(0)
        self._notify()
        return item

    def pause(self) -> None:
        if self._paused:
            return
        self._paused = True
        self._notify()

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        self._notify()

    def clear(self) -> None:
        if not self._items and not self._paused:
            return
        self._items.clear()
        self._paused = False
        self._notify()

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()
