from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual.app import App
from textual.message_pump import MessagePump

# `message_queue_size` drops on dequeue, not on handler return, so require two rounds.
_CLEAN_ROUNDS = 2

# Cap on rounds, so a widget with a live timer cannot spin the loop forever.
_MAX_ROUNDS = 32


def drain_pumps(app: App[Any], done: Callable[[], None]) -> None:
    """Run *done* once every message pump stayed idle for two rounds."""
    _round(app, 0, 0, done)


def _round(app: App[Any], index: int, clean: int, done: Callable[[], None]) -> None:
    remaining = 0

    def settled() -> None:
        nonlocal remaining
        remaining -= 1
        if remaining:
            return
        next_clean = clean + 1 if _queues_empty(app) else 0
        if next_clean >= _CLEAN_ROUNDS or index >= _MAX_ROUNDS:
            done()
            return
        _round(app, index + 1, next_clean, done)

    # Re-walked every round: a hop may mount widgets that own further work.
    for pump in _pumps(app):
        if pump.call_later(settled):
            remaining += 1
    if not remaining:
        done()


def _pumps(app: App[Any]) -> tuple[MessagePump, ...]:
    return (app, *app.screen.walk_children(with_self=True))


def _queues_empty(app: App[Any]) -> bool:
    return all(pump.message_queue_size == 0 for pump in _pumps(app))
