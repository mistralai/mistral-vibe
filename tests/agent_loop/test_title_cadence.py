from __future__ import annotations

from vibe.core.agent_loop._title_cadence import TitleCadence


def _step_count_until_due(cadence: TitleCadence) -> int:
    steps = 0
    while True:
        steps += 1
        if cadence.begin_if_due() is not None:
            return steps


class TestTitleCadence:
    def test_fires_on_the_first_step(self) -> None:
        cadence = TitleCadence(
            refresh_every=3, capped_max_generations=2, initial_max_steps=1
        )

        assert cadence.begin_if_due() is not None

    def test_not_due_again_until_the_interval_elapses(self) -> None:
        cadence = TitleCadence(
            refresh_every=3, capped_max_generations=2, initial_max_steps=1
        )
        assert cadence.begin_if_due() is not None  # step 1

        assert cadence.begin_if_due() is None  # step 2
        assert cadence.begin_if_due() is None  # step 3
        assert cadence.begin_if_due() is not None  # step 4: 3 steps later

    def test_compaction_forces_due_before_the_interval(self) -> None:
        cadence = TitleCadence(
            refresh_every=6, capped_max_generations=2, initial_max_steps=1
        )
        assert cadence.begin_if_due() is not None  # step 1
        assert cadence.begin_if_due() is None  # step 2

        cadence.mark_compaction()

        assert cadence.begin_if_due() is not None  # step 3, forced by compaction

    def test_restore_reschedules_the_next_step(self) -> None:
        cadence = TitleCadence(
            refresh_every=6, capped_max_generations=2, initial_max_steps=1
        )
        ticket = cadence.begin_if_due()
        assert ticket is not None

        cadence.restore(ticket)

        # The unsatisfied generation is retried immediately, not 6 steps later.
        assert cadence.begin_if_due() is not None

    def test_restore_preserves_a_compaction_refresh(self) -> None:
        cadence = TitleCadence(
            refresh_every=6, capped_max_generations=2, initial_max_steps=1
        )
        assert cadence.begin_if_due() is not None  # first fire
        cadence.mark_compaction()
        ticket = cadence.begin_if_due()
        assert ticket is not None
        assert ticket.due_to_compaction

        cadence.restore(ticket)

        retry = cadence.begin_if_due()
        assert retry is not None
        assert retry.due_to_compaction

    def test_reset_starts_over(self) -> None:
        cadence = TitleCadence(
            refresh_every=3, capped_max_generations=2, initial_max_steps=1
        )
        assert cadence.begin_if_due() is not None
        assert cadence.begin_if_due() is None

        cadence.reset()

        assert cadence.begin_if_due() is not None
        assert _step_count_until_due(cadence) == 3


class TestTitleCadenceInitialTiming:
    def test_fires_when_the_first_turn_completes_before_the_ceiling(self) -> None:
        cadence = TitleCadence(
            refresh_every=6, capped_max_generations=2, initial_max_steps=3
        )

        # Step 1 is still mid-turn (a tool call), so the initial title waits.
        assert cadence.begin_if_due(turn_completing=False) is None
        # Step 2 the assistant answers: the turn completes and the title fires.
        assert cadence.begin_if_due(turn_completing=True) is not None

    def test_fires_at_the_ceiling_when_the_turn_never_completes(self) -> None:
        cadence = TitleCadence(
            refresh_every=6, capped_max_generations=2, initial_max_steps=3
        )

        assert cadence.begin_if_due(turn_completing=False) is None  # step 1
        assert cadence.begin_if_due(turn_completing=False) is None  # step 2
        # Step 3 hits the ceiling, so a long tool-only turn still gets a title.
        assert cadence.begin_if_due(turn_completing=False) is not None


class TestTitleCadenceCappedTier:
    def test_fires_the_initial_but_not_the_periodic_refresh(self) -> None:
        cadence = TitleCadence(
            refresh_every=3, capped_max_generations=5, initial_max_steps=1
        )

        assert cadence.begin_if_due(periodic=False) is not None  # initial
        # No periodic refresh off the cheap tier, even well past the interval.
        for _ in range(10):
            assert cadence.begin_if_due(periodic=False) is None

    def test_still_fires_on_compaction(self) -> None:
        cadence = TitleCadence(
            refresh_every=6, capped_max_generations=5, initial_max_steps=1
        )
        assert cadence.begin_if_due(periodic=False) is not None  # initial

        cadence.mark_compaction()

        assert cadence.begin_if_due(periodic=False) is not None

    def test_caps_total_attempts(self) -> None:
        cadence = TitleCadence(
            refresh_every=6, capped_max_generations=2, initial_max_steps=1
        )
        assert cadence.begin_if_due(periodic=False) is not None  # attempt 1

        cadence.mark_compaction()
        assert cadence.begin_if_due(periodic=False) is not None  # attempt 2

        # The cap is reached; further compactions no longer trigger a call.
        cadence.mark_compaction()
        assert cadence.begin_if_due(periodic=False) is None

    def test_restore_does_not_refund_the_cap(self) -> None:
        cadence = TitleCadence(
            refresh_every=6, capped_max_generations=1, initial_max_steps=1
        )
        ticket = cadence.begin_if_due(periodic=False)
        assert ticket is not None

        # A failed attempt is rescheduled but still spent: no unbounded retry.
        cadence.restore(ticket)

        assert cadence.begin_if_due(periodic=False) is None
