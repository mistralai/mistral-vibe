from __future__ import annotations

from vibe.core.pawgress.events import IslandStatus
from vibe.overlay.server import is_attention_event


def test_entering_waiting_is_attention():
    assert is_attention_event(IslandStatus.WORKING, IslandStatus.WAITING)


def test_entering_completed_or_blocked_is_attention():
    assert is_attention_event(IslandStatus.WORKING, IslandStatus.COMPLETED)
    assert is_attention_event(IslandStatus.VERIFYING, IslandStatus.BLOCKED)


def test_first_state_waiting_is_attention():
    assert is_attention_event(None, IslandStatus.WAITING)


def test_staying_in_same_state_is_not_attention():
    assert not is_attention_event(IslandStatus.WAITING, IslandStatus.WAITING)


def test_routine_churn_is_not_attention():
    assert not is_attention_event(IslandStatus.WORKING, IslandStatus.VERIFYING)
    assert not is_attention_event(IslandStatus.VERIFYING, IslandStatus.WORKING)
    assert not is_attention_event(None, IslandStatus.WORKING)
