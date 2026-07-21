from __future__ import annotations

from vibe.core.pawgress.events import IslandState, IslandStatus
from vibe.overlay.registry import SessionRegistry


def _state(goal: str) -> IslandState:
    return IslandState(goal=goal, state=IslandStatus.WORKING)


def test_first_session_active_and_state_does_not_steal_focus():
    reg = SessionRegistry()
    reg.upsert_hello("a", "Goal A")
    reg.upsert_hello("b", "Goal B")
    assert reg.count() == 2
    assert reg.active_sid == "a"  # first session
    reg.upsert_state("b", _state("Goal B"))
    assert reg.active_sid == "a"  # background update does not steal focus
    reg.set_active("b")
    state = reg.active_state()
    assert state is not None
    assert state.goal == "Goal B"


def test_remove_active_falls_back_to_another():
    reg = SessionRegistry()
    reg.upsert_hello("a", "A")
    reg.upsert_hello("b", "B")
    assert reg.active_sid == "a"
    reg.remove("a")
    assert reg.active_sid == "b"  # falls back
    reg.remove("b")
    assert reg.active_sid is None
    assert reg.count() == 0


def test_order_is_insertion_order():
    reg = SessionRegistry()
    reg.upsert_hello("a", "A")
    reg.upsert_hello("b", "B")
    reg.upsert_hello("c", "C")
    assert reg.order() == ["a", "b", "c"]


def test_set_active_switches_and_ignores_unknown():
    reg = SessionRegistry()
    reg.upsert_hello("a", "A")
    reg.upsert_hello("b", "B")
    reg.set_active("a")
    assert reg.active_sid == "a"
    reg.set_active("zzz")  # unknown → no-op
    assert reg.active_sid == "a"


def test_upsert_state_for_unknown_creates_entry():
    reg = SessionRegistry()
    reg.upsert_state("x", _state("X goal"))
    assert reg.count() == 1
    assert reg.active_sid == "x"
    state = reg.active_state()
    assert state is not None
    assert state.goal == "X goal"


def test_activate_prev_next_wraps():
    reg = SessionRegistry()
    for sid in ("a", "b", "c"):
        reg.upsert_hello(sid, sid.upper())
    assert reg.active_sid == "a"
    reg.activate_next()
    assert reg.active_sid == "b"
    reg.activate_next()
    assert reg.active_sid == "c"
    reg.activate_next()
    assert reg.active_sid == "a"  # wraps
    reg.activate_prev()
    assert reg.active_sid == "c"  # wraps back


def test_hello_carries_terminal_and_model():
    reg = SessionRegistry()
    reg.upsert_hello("a", "Fix bug", terminal="Ghostty", model="mistral-medium")
    entry = reg.entries()[0]
    assert entry.terminal == "Ghostty"
    assert entry.model == "mistral-medium"
    assert reg.active_index() == 0
