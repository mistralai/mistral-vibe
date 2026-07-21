"""In-memory view of the sessions currently connected to the overlay.

Pure and Qt-free so it can be unit-tested. The overlay server feeds it; the
window renders the directory (all sessions) and the detail slide (active one).
"""

from __future__ import annotations

from dataclasses import dataclass

from vibe.core.pawgress.events import IslandState


@dataclass
class SessionEntry:
    sid: str
    label: str
    terminal: str = ""
    model: str = ""
    state: IslandState | None = None

    @property
    def elapsed(self) -> str | None:
        return self.state.elapsed if self.state else None


class SessionRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, SessionEntry] = {}
        self._active: str | None = None

    def upsert_hello(
        self, sid: str, label: str, terminal: str = "", model: str = ""
    ) -> None:
        entry = self._entries.get(sid)
        if entry is None:
            self._entries[sid] = SessionEntry(
                sid=sid, label=label, terminal=terminal, model=model
            )
        else:
            entry.label = label
            entry.terminal = terminal
            entry.model = model
        if self._active is None:
            self._active = sid

    def upsert_state(self, sid: str, state: IslandState) -> None:
        entry = self._entries.get(sid)
        if entry is None:
            entry = SessionEntry(sid=sid, label=state.goal)
            self._entries[sid] = entry
        entry.state = state
        # A background session's updates must not steal the tab you're viewing;
        # focus only auto-selects when nothing is selected yet.
        if self._active is None:
            self._active = sid

    def remove(self, sid: str) -> None:
        self._entries.pop(sid, None)
        if self._active == sid:
            self._active = next(iter(self._entries), None)

    @property
    def active_sid(self) -> str | None:
        return self._active

    def active_state(self) -> IslandState | None:
        entry = self._entries.get(self._active) if self._active else None
        return entry.state if entry else None

    def entries(self) -> list[SessionEntry]:
        return list(self._entries.values())

    def order(self) -> list[str]:
        return list(self._entries)

    def label_of(self, sid: str) -> str:
        entry = self._entries.get(sid)
        return entry.label if entry else sid

    def count(self) -> int:
        return len(self._entries)

    def active_index(self) -> int:
        order = self.order()
        return order.index(self._active) if self._active in order else 0

    def set_active(self, sid: str) -> None:
        if sid in self._entries:
            self._active = sid

    def activate_prev(self) -> None:
        self._step_active(-1)

    def activate_next(self) -> None:
        self._step_active(+1)

    def _step_active(self, delta: int) -> None:
        order = self.order()
        if not order:
            return
        idx = order.index(self._active) if self._active in order else 0
        self._active = order[(idx + delta) % len(order)]
