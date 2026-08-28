from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TitleGenTicket:
    """A due title generation. Hand it back to ``restore`` if it doesn't land."""

    prev_index: int
    due_to_compaction: bool


class TitleCadence:
    """Decides when a background session-title refresh is due.

    The initial title waits for the first turn to complete (the assistant
    answers instead of calling another tool) or an ``initial_max_steps`` ceiling,
    whichever comes first, so a tool-heavy opening step doesn't title off thin
    context while a genuinely long turn still gets a title.

    After that, on the cheap tier (``periodic=True``) it refreshes every
    ``refresh_every`` steps and after a compaction. Off the cheap tier
    (``periodic=False``, e.g. the title falls back to the possibly-expensive
    active model) it drops the periodic refresh and caps total attempts at
    ``capped_max_generations``, so it only titles at the start and after a
    compaction, a handful of times at most.

    A generation that does not land is restored so the next step retries; the
    attempt still counts against the cap, so a failing expensive model cannot be
    retried without bound. Keeps this policy and its state out of AgentLoop,
    which stays about turns.
    """

    def __init__(
        self, *, refresh_every: int, capped_max_generations: int, initial_max_steps: int
    ) -> None:
        self._refresh_every = refresh_every
        self._capped_max = capped_max_generations
        self._initial_max_steps = initial_max_steps
        self._step_index = 0
        self._last_gen_index = 0
        self._compacted_since_gen = False
        self._generations = 0

    def mark_compaction(self) -> None:
        self._compacted_since_gen = True

    def begin_if_due(
        self, *, periodic: bool = True, turn_completing: bool = False
    ) -> TitleGenTicket | None:
        """Advance one model step; return a ticket when a refresh is due."""
        self._step_index += 1
        if not periodic and self._generations >= self._capped_max:
            return None
        initial_pending = self._last_gen_index == 0
        initial_due = initial_pending and (
            turn_completing or self._step_index >= self._initial_max_steps
        )
        due = (
            initial_due
            or self._compacted_since_gen
            or (
                periodic
                and not initial_pending
                and self._step_index - self._last_gen_index >= self._refresh_every
            )
        )
        if not due:
            return None
        ticket = TitleGenTicket(
            prev_index=self._last_gen_index, due_to_compaction=self._compacted_since_gen
        )
        self._last_gen_index = self._step_index
        self._compacted_since_gen = False
        self._generations += 1
        return ticket

    def restore(self, ticket: TitleGenTicket) -> None:
        # Reschedule the next step, but keep the spent attempt counted so a
        # capped tier cannot retry an expensive model without bound.
        self._last_gen_index = ticket.prev_index
        if ticket.due_to_compaction:
            self._compacted_since_gen = True

    def reset(self) -> None:
        self._step_index = 0
        self._last_gen_index = 0
        self._compacted_since_gen = False
        self._generations = 0
