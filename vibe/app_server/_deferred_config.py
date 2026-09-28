"""Configuration a running turn cannot read, held until the next turn boundary.

The Rust Core takes its settings when a turn starts, so a configuration written
during one cannot reach it. Refusing the write would lose a choice the user has
already made, so the Host parks the derivation here instead and settles it at
the next boundary: the turn ending, the next turn opening, or the Harness
promoting a queued turn.

Every transition of that parked state lives in this class. Losing a race with a
turn is one of them: a turn can start while the derivation is being computed, or
between the check and the Session taking its own lock, and either way the change
goes back to being parked rather than raising at the caller -- one of them is the
event pump, where an exception ends the session's live stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib

from mistralai_vibe_local_harness.vibe import HarnessTurnConflictError


class DeferredConfiguration[DerivationT]:
    """Holds the one derivation a running turn is keeping the session from."""

    def __init__(
        self,
        *,
        derive: Callable[[], Awaitable[DerivationT]],
        push: Callable[[DerivationT], Awaitable[None]],
        adopt: Callable[[DerivationT], None],
        turn_running: Callable[[], bool],
    ) -> None:
        self._derive = derive
        self._push = push
        self._adopt = adopt
        self._turn_running = turn_running
        self._parked = False
        self._applying = asyncio.Lock()

    @property
    def parked(self) -> bool:
        """Whether a change is waiting for a turn boundary."""
        return self._parked

    def park(self) -> None:
        """Hold the current configuration back until the next boundary."""
        self._parked = True

    def mark_applied(self) -> None:
        """Record that a caller pushed the configuration by its own route."""
        self._parked = False

    @contextlib.asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        """Hold off applies while the caller pushes a configuration of its own.

        An agent switch derives and pushes for itself, because only part of it
        can land mid-turn. Outside this, an apply that derived before the switch
        can push after it and put the session back on what the user moved off.
        """
        async with self._applying:
            yield

    async def apply(self) -> None:
        """Derive and push now, parking again if a turn owns the settings.

        Serialised: deriving and pushing are two awaits, so concurrent callers
        could otherwise push in the reverse order and leave the session running
        a configuration older than the one that is persisted. Waiting here means
        the second caller derives after the first has pushed, and so reads the
        configuration that won.
        """
        async with self._applying:
            await self._apply_locked()

    async def _apply_locked(
        self,
        push: Callable[[DerivationT], Awaitable[None]] | None = None,
        *,
        check_turn: bool = True,
    ) -> None:
        self._parked = False
        try:
            derivation = await self._derive()
            if check_turn and self._turn_running():
                self._parked = True
                return
            await (push or self._push)(derivation)
        except HarnessTurnConflictError:
            self._parked = True
            return
        except BaseException:
            # Parked until it is actually running. One caller is the event pump,
            # whose task is cancelled when the session's stream ends, and a
            # cancellation between clearing the flag and pushing would otherwise
            # drop a pick the user has already made.
            self._parked = True
            raise
        self._adopt(derivation)

    async def apply_when_idle(self) -> None:
        """Push between turns; park during one."""
        if self._turn_running():
            self._parked = True
            return
        await self.apply()

    async def apply_through(
        self, push: Callable[[DerivationT], Awaitable[None]]
    ) -> None:
        """Derive and push through a push of the caller's own.

        A turn the Session has reserved but not started is one the Core has not
        taken settings for, so a configuration can still land on it -- but only
        the caller knows that, and only a push that says so is accepted. The
        lock is what this shares with the ordinary apply.
        """
        async with self._applying:
            await self._apply_locked(push, check_turn=False)

    async def settle_through(
        self, push: Callable[[DerivationT], Awaitable[None]]
    ) -> bool:
        """Land what is parked through a push of the caller's own.

        Takes the lock before reading the flag for the reason ``settle`` does:
        an apply clears it before it pushes, so reading it outside would step
        past a push still in flight -- and here that push is the one a reserved
        turn is about to run on.
        """
        async with self._applying:
            if not self._parked:
                return False
            await self._apply_locked(push, check_turn=False)
            return not self._parked

    async def settle(self) -> bool:
        """Apply what is parked, if a boundary has actually been reached.

        Answers whether the session moved, so the caller can announce a
        configuration nobody has been told about: the write that parked it
        answered ``pending`` and said nothing since.

        Takes the lock before reading the flag rather than after: an apply
        clears it before it pushes, so a settle that only read the flag would
        return while that push was still in flight. This one is a turn asking
        whether it may open, and it would have opened on the configuration
        being replaced -- leaving the apply to find a turn running and park
        what it had already taken responsibility for.
        """
        async with self._applying:
            if not self._parked or self._turn_running():
                return False
            await self._apply_locked()
            return not self._parked
