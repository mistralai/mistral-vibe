from __future__ import annotations

from vibe.core.pawgress.events import ControlAction, IslandState, IslandStatus
from vibe.core.pawgress.protocol import (
    ByeMsg,
    ControlMsg,
    HelloMsg,
    StateMsg,
    decode_line,
    encode_line,
)


def _roundtrip(msg):
    return decode_line(encode_line(msg))


def test_hello_roundtrip():
    msg = HelloMsg(
        sid="abc", label="Fix bug", pid=123, terminal="Ghostty", model="mistral-medium"
    )
    assert _roundtrip(msg) == msg


def test_hello_roundtrip_defaults():
    assert _roundtrip(HelloMsg(sid="abc", label="Fix bug", pid=123)) == HelloMsg(
        sid="abc", label="Fix bug", pid=123, terminal="", model=""
    )


def test_state_roundtrip():
    state = IslandState(goal="g", state=IslandStatus.WORKING, context_tokens=10)
    out = _roundtrip(StateMsg(sid="abc", state=state))
    assert isinstance(out, StateMsg)
    assert out.sid == "abc"
    assert out.state.goal == "g"
    assert out.state.context_tokens == 10


def test_bye_roundtrip():
    assert _roundtrip(ByeMsg(sid="abc")) == ByeMsg(sid="abc")


def test_control_roundtrip():
    out = _roundtrip(ControlMsg(action=ControlAction.STOP, request_id="r1"))
    assert out == ControlMsg(action=ControlAction.STOP, request_id="r1")


def test_decode_garbage_returns_none():
    assert decode_line("not json") is None
    assert decode_line('{"t": "unknown"}') is None
    assert decode_line("") is None
