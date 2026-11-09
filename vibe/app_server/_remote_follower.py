"""A session's view of a remote value its process refreshes.

A process service holds the value and refreshes it for every session. A
session follows it with a ``RemoteFollower``, which only remembers which value
the session's configuration was last built from and tells whether the one held
now is different. It never applies anything: the session loads the value into
its configuration at a turn boundary, where everything else it runs converges.
A remote value is an input to that one step, not a step of its own.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol


class FollowedValue[V](Protocol):
    """What a follower needs of the service holding the value."""

    def held(self) -> V | None:
        """The value held now, without waiting; ``None`` while there is none."""
        ...

    def check(self) -> None:
        """Start a refresh when one is due; never waits."""
        ...


class RemoteFollower[V]:
    def __init__(self, source: FollowedValue[V], *, loaded: V | None) -> None:
        self._source = source
        self._loaded = loaded
        self._forced = False

    @property
    def loaded(self) -> V | None:
        """The value the session's configuration was last built from."""
        return self._loaded

    @property
    def forced(self) -> bool:
        """Whether ``/reload`` asked for the value held, even one refused."""
        return self._forced

    @property
    def due(self) -> bool:
        """Whether the next boundary has a value to load."""
        held = self._source.held()
        return held is not None and (self._forced or held != self._loaded)

    def take(self) -> V | None:
        """The value to load at this boundary, or ``None`` when none is due."""
        return self._source.held() if self.due else None

    def mark_loaded(self, value: V) -> None:
        """Record that the session loaded ``value``, or refused it for good."""
        self._loaded = value
        self._forced = False

    async def refresh(self, fetch: Callable[[], Awaitable[object]]) -> None:
        """Fetch now (``/reload``) and have the value held then loaded, even one
        loaded or refused before: what it is checked against may have changed.
        """
        await fetch()
        self._forced = True

    def check(self) -> None:
        self._source.check()


__all__ = ["FollowedValue", "RemoteFollower"]
