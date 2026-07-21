"""NDJSON wire protocol between a Vibe session (client) and the overlay broker.

One JSON object per line, ``\\n``-terminated. Client→overlay: hello / state /
bye. Overlay→client: control. Reuses ``IslandState`` and ``ControlAction`` so
there is a single source of truth for the payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from vibe.core.pawgress.events import ControlAction, IslandState


@dataclass(frozen=True)
class HelloMsg:
    sid: str
    label: str
    pid: int
    terminal: str = ""
    model: str = ""


@dataclass(frozen=True)
class StateMsg:
    sid: str
    state: IslandState


@dataclass(frozen=True)
class ByeMsg:
    sid: str


@dataclass(frozen=True)
class ControlMsg:
    action: ControlAction
    request_id: str | None = None


Message = HelloMsg | StateMsg | ByeMsg | ControlMsg


def encode_line(msg: Message) -> str:
    match msg:
        case HelloMsg(sid, label, pid, terminal, model):
            obj: dict = {
                "t": "hello",
                "sid": sid,
                "label": label,
                "pid": pid,
                "terminal": terminal,
                "model": model,
            }
        case StateMsg(sid, state):
            obj = {"t": "state", "sid": sid, "state": state.model_dump(mode="json")}
        case ByeMsg(sid):
            obj = {"t": "bye", "sid": sid}
        case ControlMsg(action, request_id):
            obj = {"t": "control", "action": action.value, "request_id": request_id}
    return json.dumps(obj) + "\n"


def _decode_obj(obj: dict) -> Message | None:
    match obj.get("t"):
        case "hello":
            return HelloMsg(
                sid=obj["sid"],
                label=obj["label"],
                pid=obj["pid"],
                terminal=obj.get("terminal", ""),
                model=obj.get("model", ""),
            )
        case "state":
            return StateMsg(
                sid=obj["sid"], state=IslandState.model_validate(obj["state"])
            )
        case "bye":
            return ByeMsg(sid=obj["sid"])
        case "control":
            return ControlMsg(
                action=ControlAction(obj["action"]), request_id=obj.get("request_id")
            )
    return None


def decode_line(line: str) -> Message | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        return _decode_obj(obj)
    except (KeyError, ValueError):
        return None
