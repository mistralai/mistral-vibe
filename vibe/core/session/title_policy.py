from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TitlePolicy:
    """The tunable numbers behind background session titles, in one place.

    Grouped by concern: when a title is (re)generated (cadence), how much of the
    conversation the model sees (transcript), the request budgets, and how the
    result is shaped. Construct a variant to tweak; ``DEFAULT_TITLE_POLICY`` is
    the shipped configuration.
    """

    # Cadence (consumed by TitleCadence). The first title waits for the opening
    # turn to finish or ``initial_max_steps``, whichever comes first. After that,
    # a cheap model refreshes every ``refresh_every_steps`` and on compaction; a
    # fallback to the active model drops the periodic refresh and is bounded to
    # ``capped_max_generations`` calls per session.
    refresh_every_steps: int = 6
    capped_max_generations: int = 2
    initial_max_steps: int = 3

    # Transcript window fed to the model: the opening intent plus the latest
    # exchange, each message clamped so one large tool result can't crowd it out.
    max_transcript_chars: int = 6000
    head_transcript_chars: int = 1500
    max_message_chars: int = 2000

    # Request budgets (seconds) and response size for the background call.
    request_timeout_seconds: float = 6.0
    retry_budget_seconds: float = 10.0
    total_timeout_seconds: float = 20.0
    max_tokens: int = 96

    # Result cleaning: hard length cap and titles treated as "no usable answer".
    max_title_chars: int = 72
    generic_titles: frozenset[str] = frozenset({
        "new session",
        "untitled session",
        "untitled",
    })

    @property
    def tail_transcript_chars(self) -> int:
        return self.max_transcript_chars - self.head_transcript_chars


DEFAULT_TITLE_POLICY = TitlePolicy()
