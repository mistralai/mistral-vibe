# Pawgress Multi-Session Overlay (socket) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Multiple concurrent Vibe sessions (separate processes/terminal tabs) each set a Pawgress goal and appear in ONE shared floating overlay; the overlay lets you tab between sessions and lives until the last participating Vibe process exits.

**Architecture:** Replace the file-tail IPC with a Unix-domain-socket broker. The overlay is a singleton `QLocalServer`; each Vibe session holds one persistent `asyncio` unix-socket connection. The connection IS the reference count — kernel closes it on crash/exit, the overlay drops that session, and the overlay quits a short grace period after the last connection closes. A lifetime-held `flock` elects the single overlay; a random UUID (not PID) identifies each session.

**Tech Stack:** Python 3.12, asyncio (Vibe/Textual client side), PySide6 `QLocalServer`/`QLocalSocket` (overlay side), `fcntl.flock` for singleton election, NDJSON over the socket. pytest.

## Global Constraints

- **Do not break the single-session experience.** One session must behave exactly as before (island appears, updates, approvals, controls, dies with the session).
- Socket path: `VIBE_HOME.path / "pawgress" / "overlay.sock"`. Lock path: `.../pawgress/overlay.lock`.
- Session identity is a random UUID (`uuid4().hex`), never the PID. PID may be sent as diagnostic metadata only.
- Wire format is NDJSON: one JSON object per line, `\n`-terminated.
- Reuse existing `IslandState` (state payload) and `ControlAction` (control payload) models — do not duplicate them.
- The overlay process holds the `flock` for its entire lifetime (kernel releases on crash → unambiguous stale cleanup).
- Overlay exits `GRACE_SECONDS = 2.0` after the session count reaches zero (covers startup-before-first-connect and reconnects).
- No new third-party dependencies. `fcntl` is stdlib (POSIX only — this is a macOS/Linux feature; matches the existing overlay which is already POSIX-only).
- Tests: `uv run pytest ...`. Repo has pre-commit (pyright/ruff); fix hook output before считая done.
- Commit messages use `feat:`/`test:`/`refactor:` prefixes. (User commits manually — do not run git commit.)

---

## File Structure

**New files:**
- `vibe/core/pawgress/protocol.py` — pure NDJSON message types + encode/decode (client↔overlay).
- `vibe/overlay/registry.py` — pure `SessionRegistry`: tracks `{sid → SessionEntry}`, active selection, refcount. No Qt.
- `vibe/overlay/singleton.py` — `acquire_overlay_lock()` (flock election) + socket-path helpers. Thin, POSIX.
- `vibe/overlay/server.py` — `OverlayServer`: `QLocalServer` wrapper; owns connections, feeds `SessionRegistry`, emits Qt signals to the window, routes control back to the right socket.
- `vibe/cli/pawgress/client.py` — `PawgressClient`: asyncio unix-socket client (connect-or-launch, send hello/state/bye, receive control).

**Modified files:**
- `vibe/core/paths.py` (or a small helper) — expose the pawgress socket/lock paths (only if not trivially derived; otherwise derive inline).
- `vibe/overlay/__main__.py` — flock election, start `OverlayServer` instead of `FileTailReader`, wire to window.
- `vibe/overlay/window.py` — Phase 2: tab bar + which session is rendered. Phase 1: unchanged rendering, driven by server signal.
- `vibe/cli/pawgress/launcher.py` — repurpose: spawn overlay candidate (no `_kill_stale`, no atexit-terminate; the socket close deregisters).
- `vibe/cli/pawgress/__init__.py` — export `PawgressClient`.
- `vibe/cli/textual_ui/app.py` — replace `PawgressSink` writes with `PawgressClient` sends; replace `_poll_pawgress_control` file polling with client control callback; drop overlay-launch/atexit assumptions.

**Deleted/retired after migration:**
- `vibe/cli/pawgress/sink.py` (`PawgressSink`) and `vibe/overlay/reader.py` (`FileTailReader`) — removed once the client/server replace them. `StdinReader` in `reader.py` is only used by the demo `stub_feed.py` path; keep a stdin fallback in `__main__` for `stub_feed.py` (see Task 9).

---

## PHASE 1 — Socket foundation (single visible island, refcounted lifecycle)

### Task 1: NDJSON protocol module

**Files:**
- Create: `vibe/core/pawgress/protocol.py`
- Test: `tests/pawgress/test_protocol.py`

**Interfaces:**
- Produces:
  - `HelloMsg(sid: str, label: str, pid: int)`
  - `StateMsg(sid: str, state: IslandState)`
  - `ByeMsg(sid: str)`
  - `ControlMsg(action: ControlAction, request_id: str | None)`
  - `encode_line(msg: HelloMsg | StateMsg | ByeMsg | ControlMsg) -> str` (JSON + `"\n"`)
  - `decode_line(line: str) -> HelloMsg | StateMsg | ByeMsg | ControlMsg | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/pawgress/test_protocol.py
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
    assert _roundtrip(HelloMsg(sid="abc", label="Fix bug", pid=123)) == HelloMsg(
        sid="abc", label="Fix bug", pid=123
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pawgress/test_protocol.py -q`
Expected: FAIL (ImportError: cannot import name 'HelloMsg')

- [ ] **Step 3: Implement**

```python
# vibe/core/pawgress/protocol.py
from __future__ import annotations

from dataclasses import dataclass
import json

from vibe.core.pawgress.events import ControlAction, IslandState


@dataclass(frozen=True)
class HelloMsg:
    sid: str
    label: str
    pid: int


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
        case HelloMsg(sid, label, pid):
            obj = {"t": "hello", "sid": sid, "label": label, "pid": pid}
        case StateMsg(sid, state):
            obj = {"t": "state", "sid": sid, "state": state.model_dump(mode="json")}
        case ByeMsg(sid):
            obj = {"t": "bye", "sid": sid}
        case ControlMsg(action, request_id):
            obj = {"t": "control", "action": action.value, "request_id": request_id}
    return json.dumps(obj) + "\n"


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
    match obj.get("t"):
        case "hello":
            return HelloMsg(sid=obj["sid"], label=obj["label"], pid=obj["pid"])
        case "state":
            return StateMsg(
                sid=obj["sid"], state=IslandState.model_validate(obj["state"])
            )
        case "bye":
            return ByeMsg(sid=obj["sid"])
        case "control":
            return ControlMsg(
                action=ControlAction(obj["action"]),
                request_id=obj.get("request_id"),
            )
        case _:
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pawgress/test_protocol.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add vibe/core/pawgress/protocol.py tests/pawgress/test_protocol.py
git commit -m "feat: NDJSON protocol for pawgress overlay socket"
```

---

### Task 2: Session registry (pure, no Qt)

**Files:**
- Create: `vibe/overlay/registry.py`
- Test: `tests/pawgress/test_registry.py`

**Interfaces:**
- Consumes: `IslandState` (events).
- Produces:
  - `SessionEntry(sid: str, label: str, state: IslandState | None)`
  - `SessionRegistry` with:
    - `upsert_hello(sid: str, label: str) -> None`
    - `upsert_state(sid: str, state: IslandState) -> None` (also makes `sid` the active session)
    - `remove(sid: str) -> None`
    - `active_sid: str | None` (property)
    - `active_state() -> IslandState | None`
    - `order() -> list[str]` (sids in insertion order — Phase 2 tab order)
    - `count() -> int`
    - `set_active(sid: str) -> None` (Phase 2 tab switching; no-op if unknown sid)

- [ ] **Step 1: Write the failing test**

```python
# tests/pawgress/test_registry.py
from __future__ import annotations

from vibe.core.pawgress.events import IslandState, IslandStatus
from vibe.overlay.registry import SessionRegistry


def _state(goal: str) -> IslandState:
    return IslandState(goal=goal, state=IslandStatus.WORKING)


def test_upsert_state_sets_active_and_counts():
    reg = SessionRegistry()
    reg.upsert_hello("a", "Goal A")
    reg.upsert_hello("b", "Goal B")
    assert reg.count() == 2
    reg.upsert_state("a", _state("Goal A"))
    assert reg.active_sid == "a"
    reg.upsert_state("b", _state("Goal B"))
    assert reg.active_sid == "b"
    assert reg.active_state().goal == "Goal B"


def test_remove_active_falls_back_to_another():
    reg = SessionRegistry()
    reg.upsert_hello("a", "A")
    reg.upsert_hello("b", "B")
    reg.upsert_state("b", _state("B"))
    assert reg.active_sid == "b"
    reg.remove("b")
    assert reg.active_sid == "a"  # falls back
    reg.remove("a")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pawgress/test_registry.py -q`
Expected: FAIL (ImportError: cannot import name 'SessionRegistry')

- [ ] **Step 3: Implement**

```python
# vibe/overlay/registry.py
from __future__ import annotations

from dataclasses import dataclass

from vibe.core.pawgress.events import IslandState


@dataclass
class SessionEntry:
    sid: str
    label: str
    state: IslandState | None = None


class SessionRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, SessionEntry] = {}
        self._active: str | None = None

    def upsert_hello(self, sid: str, label: str) -> None:
        if sid in self._entries:
            self._entries[sid].label = label
        else:
            self._entries[sid] = SessionEntry(sid=sid, label=label)
        if self._active is None:
            self._active = sid

    def upsert_state(self, sid: str, state: IslandState) -> None:
        entry = self._entries.get(sid)
        if entry is None:
            entry = SessionEntry(sid=sid, label=state.goal)
            self._entries[sid] = entry
        entry.state = state
        self._active = sid

    def remove(self, sid: str) -> None:
        self._entries.pop(sid, None)
        if self._active == sid:
            self._active = next(iter(self._entries), None)

    @property
    def active_sid(self) -> str | None:
        return self._active

    def active_state(self) -> IslandState | None:
        if self._active is None:
            return None
        entry = self._entries.get(self._active)
        return entry.state if entry else None

    def order(self) -> list[str]:
        return list(self._entries)

    def count(self) -> int:
        return len(self._entries)

    def set_active(self, sid: str) -> None:
        if sid in self._entries:
            self._active = sid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pawgress/test_registry.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add vibe/overlay/registry.py tests/pawgress/test_registry.py
git commit -m "feat: pawgress overlay session registry"
```

---

### Task 3: Singleton election + paths

**Files:**
- Create: `vibe/overlay/singleton.py`
- Test: `tests/pawgress/test_singleton.py`

**Interfaces:**
- Produces:
  - `pawgress_dir() -> Path` (`VIBE_HOME.path / "pawgress"`, created)
  - `socket_path() -> Path` (`pawgress_dir() / "overlay.sock"`)
  - `lock_path() -> Path` (`pawgress_dir() / "overlay.lock"`)
  - `acquire_overlay_lock() -> int | None` — non-blocking `flock`; returns the held fd on success, `None` if another overlay owns it. Caller keeps the fd for the process lifetime.

- [ ] **Step 1: Write the failing test**

```python
# tests/pawgress/test_singleton.py
from __future__ import annotations

import os

from vibe.overlay import singleton


def test_second_acquire_fails_while_first_held(tmp_path, monkeypatch):
    monkeypatch.setattr(singleton, "pawgress_dir", lambda: tmp_path)
    fd = singleton.acquire_overlay_lock()
    assert fd is not None
    # A second attempt from the same process must fail (flock is exclusive).
    assert singleton.acquire_overlay_lock() is None
    os.close(fd)
    # After release, acquiring works again.
    fd2 = singleton.acquire_overlay_lock()
    assert fd2 is not None
    os.close(fd2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pawgress/test_singleton.py -q`
Expected: FAIL (AttributeError/ImportError)

- [ ] **Step 3: Implement**

```python
# vibe/overlay/singleton.py
from __future__ import annotations

import fcntl
import os
from pathlib import Path

from vibe.core.paths import VIBE_HOME


def pawgress_dir() -> Path:
    path = VIBE_HOME.path / "pawgress"
    path.mkdir(parents=True, exist_ok=True)
    return path


def socket_path() -> Path:
    return pawgress_dir() / "overlay.sock"


def lock_path() -> Path:
    return pawgress_dir() / "overlay.lock"


def acquire_overlay_lock() -> int | None:
    """Non-blocking exclusive flock. Returns the held fd, or None if taken.

    The caller must keep the fd open for the overlay's whole lifetime; the
    kernel releases it automatically on process death.
    """
    fd = os.open(str(lock_path()), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pawgress/test_singleton.py -q`
Expected: PASS

Note: same-process re-`flock` on a *new* fd for the same file DOES conflict under `LOCK_NB` on Linux/macOS (flock is per-open-file-description; a second `open`+`flock` fails). This is what the test asserts.

- [ ] **Step 5: Commit**

```bash
git add vibe/overlay/singleton.py tests/pawgress/test_singleton.py
git commit -m "feat: flock singleton election + socket paths for overlay"
```

---

### Task 4: Overlay server (QLocalServer wrapper)

**Files:**
- Create: `vibe/overlay/server.py`
- Test: manual (Qt/socket integration — see Task 9 verification). A thin pure part (`SessionRegistry`) is already tested.

**Interfaces:**
- Consumes: `SessionRegistry` (Task 2), `decode_line`/`encode_line` + `ControlMsg` (Task 1).
- Produces: `OverlayServer(QObject)` with:
  - `Signal state_changed` (emitted after any registry change; the window connects it and re-renders `registry.active_state()`).
  - `Signal empty` (emitted when `registry.count()` hits 0).
  - `listen(socket_path: str) -> bool`
  - `send_control(sid: str, msg: ControlMsg) -> None`
  - `registry: SessionRegistry` (attribute)

**Implementation spec (write this file):**

```python
# vibe/overlay/server.py
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from vibe.core.pawgress.protocol import (
    ByeMsg,
    ControlMsg,
    HelloMsg,
    StateMsg,
    decode_line,
    encode_line,
)
from vibe.overlay.registry import SessionRegistry


class OverlayServer(QObject):
    state_changed = Signal()
    empty = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.registry = SessionRegistry()
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._sockets: dict[str, QLocalSocket] = {}   # sid -> socket
        self._sid_of: dict[int, str] = {}             # id(socket) -> sid
        self._buffers: dict[int, bytes] = {}          # id(socket) -> partial line buffer

    def listen(self, socket_path: str) -> bool:
        QLocalServer.removeServer(socket_path)  # clear a stale path (we hold the flock)
        return self._server.listen(socket_path)

    def send_control(self, sid: str, msg: ControlMsg) -> None:
        sock = self._sockets.get(sid)
        if sock is not None and sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
            sock.write(encode_line(msg).encode("utf-8"))
            sock.flush()

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[id(sock)] = b""
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))

    def _on_ready_read(self, sock: QLocalSocket) -> None:
        self._buffers[id(sock)] += bytes(sock.readAll().data())
        buf = self._buffers[id(sock)]
        while b"\n" in buf:
            raw, _, buf = buf.partition(b"\n")
            self._handle_line(sock, raw.decode("utf-8", "replace"))
        self._buffers[id(sock)] = buf

    def _handle_line(self, sock: QLocalSocket, line: str) -> None:
        msg = decode_line(line)
        if isinstance(msg, HelloMsg):
            self._sockets[msg.sid] = sock
            self._sid_of[id(sock)] = msg.sid
            self.registry.upsert_hello(msg.sid, msg.label)
            self.state_changed.emit()
        elif isinstance(msg, StateMsg):
            self._sockets[msg.sid] = sock
            self._sid_of[id(sock)] = msg.sid
            self.registry.upsert_state(msg.sid, msg.state)
            self.state_changed.emit()
        elif isinstance(msg, ByeMsg):
            self._drop(msg.sid)

    def _on_disconnected(self, sock: QLocalSocket) -> None:
        sid = self._sid_of.pop(id(sock), None)
        self._buffers.pop(id(sock), None)
        if sid is not None:
            self._drop(sid)
        sock.deleteLater()

    def _drop(self, sid: str) -> None:
        self.registry.remove(sid)
        self._sockets.pop(sid, None)
        self.state_changed.emit()
        if self.registry.count() == 0:
            self.empty.emit()
```

- [ ] **Step 1: Write the file above verbatim.**
- [ ] **Step 2: Import-check.** Run: `uv run python -c "import vibe.overlay.server"` — Expected: no error (PySide6 QtNetwork imports).
- [ ] **Step 3: Commit**

```bash
git add vibe/overlay/server.py
git commit -m "feat: QLocalServer overlay broker over NDJSON"
```

---

### Task 5: PawgressClient (asyncio unix socket, connect-or-launch)

**Files:**
- Create: `vibe/cli/pawgress/client.py`
- Modify: `vibe/cli/pawgress/launcher.py` (repurpose `launch_overlay` → `spawn_overlay_candidate`, drop `_kill_stale`/atexit-terminate)
- Test: `tests/pawgress/test_client_connect.py` (real unix socket, no Qt)

**Interfaces:**
- Consumes: `encode_line`/`decode_line`, `HelloMsg`/`StateMsg`/`ByeMsg`/`ControlMsg` (Task 1); `socket_path()`, `spawn_overlay_candidate()`.
- Produces: `PawgressClient` with:
  - `__init__(self, on_control: Callable[[ControlMsg], None], label: str)`
  - `async def connect(self) -> None` — connect to `socket_path()`, launching+retrying if absent; sends `HelloMsg` on connect; starts a background reader task that decodes control lines and calls `on_control`.
  - `def send_state(self, state: IslandState) -> None` — enqueue a `StateMsg` (fire-and-forget write).
  - `async def close(self) -> None` — send `ByeMsg`, close the writer.
  - `sid: str` (the random uuid)

- [ ] **Step 1: Write the failing test** (a minimal in-process echo server proves connect + hello + control receipt, no Qt/subprocess)

```python
# tests/pawgress/test_client_connect.py
from __future__ import annotations

import asyncio

import pytest

from vibe.core.pawgress.events import ControlAction, IslandState, IslandStatus
from vibe.core.pawgress.protocol import (
    ControlMsg,
    HelloMsg,
    StateMsg,
    decode_line,
    encode_line,
)
from vibe.cli.pawgress import client as client_mod


@pytest.mark.asyncio
async def test_client_connects_sends_hello_and_receives_control(tmp_path, monkeypatch):
    sock_file = tmp_path / "overlay.sock"
    monkeypatch.setattr(client_mod, "socket_path", lambda: sock_file)
    # If the client tries to launch, that's a failure for this test — server is already up.
    monkeypatch.setattr(client_mod, "spawn_overlay_candidate", lambda: None)

    received: list = []
    got_control = asyncio.Event()

    async def handle(reader, writer):
        # read hello, then push a control line back
        line = await reader.readline()
        received.append(decode_line(line.decode()))
        writer.write(encode_line(ControlMsg(action=ControlAction.STOP)))
        await writer.drain()

    server = await asyncio.start_unix_server(handle, path=str(sock_file))

    controls: list = []

    def on_control(msg: ControlMsg) -> None:
        controls.append(msg)
        got_control.set()

    client = client_mod.PawgressClient(on_control=on_control, label="Fix bug")
    await client.connect()
    client.send_state(IslandState(goal="g", state=IslandStatus.WORKING))
    await asyncio.wait_for(got_control.wait(), timeout=2.0)
    await client.close()
    server.close()
    await server.wait_closed()

    assert isinstance(received[0], HelloMsg)
    assert received[0].label == "Fix bug"
    assert controls[0].action == ControlAction.STOP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pawgress/test_client_connect.py -q`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement client + repurpose launcher**

`vibe/cli/pawgress/launcher.py` — replace the whole module body:

```python
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import subprocess
import sys

from vibe.core.logger import logger


def spawn_overlay_candidate() -> None:
    """Start an overlay candidate. It self-elects via flock; losers exit.
    Safe to call from multiple sessions — the flock guarantees one winner."""
    try:
        subprocess.Popen(
            [sys.executable, "-m", "vibe.overlay"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        logger.warning("Failed to spawn pawgress overlay: %s", e)
```

`vibe/cli/pawgress/client.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import os
from uuid import uuid4

from vibe.core.logger import logger
from vibe.core.pawgress.events import IslandState
from vibe.core.pawgress.protocol import (
    ByeMsg,
    ControlMsg,
    HelloMsg,
    StateMsg,
    decode_line,
)
from vibe.core.pawgress.protocol import encode_line
from vibe.cli.pawgress.launcher import spawn_overlay_candidate
from vibe.overlay.singleton import socket_path

_CONNECT_ATTEMPTS = 30
_CONNECT_DELAY = 0.1


class PawgressClient:
    def __init__(self, on_control: Callable[[ControlMsg], None], label: str) -> None:
        self.sid = uuid4().hex
        self._on_control = on_control
        self._label = label
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None

    async def connect(self) -> None:
        reader, writer = await self._connect_or_launch()
        if writer is None or reader is None:
            return
        self._writer = writer
        self._write(HelloMsg(sid=self.sid, label=self._label, pid=os.getpid()))
        self._reader_task = asyncio.create_task(self._read_loop(reader))

    async def _connect_or_launch(self):
        path = str(socket_path())
        launched = False
        for attempt in range(_CONNECT_ATTEMPTS):
            try:
                return await asyncio.open_unix_connection(path)
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                if not launched:
                    spawn_overlay_candidate()
                    launched = True
                await asyncio.sleep(_CONNECT_DELAY)
        logger.warning("Pawgress overlay did not come up; continuing headless")
        return None, None

    def _write(self, msg) -> None:
        if self._writer is None:
            return
        try:
            self._writer.write(encode_line(msg).encode("utf-8"))
        except (RuntimeError, ConnectionError):
            self._writer = None

    def send_state(self, state: IslandState) -> None:
        self._write(StateMsg(sid=self.sid, state=state))

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                line = await reader.readline()
            except (ConnectionError, OSError):
                break
            if not line:
                break
            msg = decode_line(line.decode("utf-8", "replace"))
            if isinstance(msg, ControlMsg):
                self._on_control(msg)

    async def close(self) -> None:
        self._write(ByeMsg(sid=self.sid))
        if self._writer is not None:
            with contextlib.suppress(Exception):
                await self._writer.drain()
                self._writer.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pawgress/test_client_connect.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/cli/pawgress/client.py vibe/cli/pawgress/launcher.py tests/pawgress/test_client_connect.py
git commit -m "feat: PawgressClient unix-socket connect-or-launch"
```

---

### Task 6: Overlay `__main__` — flock election + server + grace exit

**Files:**
- Modify: `vibe/overlay/__main__.py`

**Interfaces:**
- Consumes: `acquire_overlay_lock`, `socket_path` (Task 3); `OverlayServer` (Task 4); `IslandWindow` (existing); `StdinReader` (kept for `stub_feed.py`).

- [ ] **Step 1: Rewrite `main()`** to: parse `--stdin` (demo) flag; if not stdin mode, acquire flock (exit 0 if lost), start `OverlayServer.listen(socket_path())`, wire `server.state_changed` → `window.update_state(server.registry.active_state())` (guard None), wire `server.empty` → a 2s `QTimer.singleShot` that quits only if still empty, wire window control clicks → `server.send_control(active_sid, ControlMsg(...))`. Keep the hotkey bridge.

```python
# vibe/overlay/__main__.py
from __future__ import annotations

import argparse
import os
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from vibe.core.pawgress.protocol import ControlMsg
from vibe.overlay.hotkey import HotkeyBridge, install_toggle_hotkey
from vibe.overlay.macos import hide_dock_icon, make_visible_on_all_spaces
from vibe.overlay.reader import StdinReader
from vibe.overlay.server import OverlayServer
from vibe.overlay.singleton import acquire_overlay_lock, socket_path
from vibe.overlay.window import IslandWindow

_GRACE_MS = 2000


def main() -> None:
    parser = argparse.ArgumentParser(prog="vibe.overlay")
    parser.add_argument("--stdin", action="store_true", help="demo mode: read stub feed")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    window = IslandWindow()
    window.show()
    make_visible_on_all_spaces(window)
    hide_dock_icon()
    QTimer.singleShot(200, hide_dock_icon)

    bridge = HotkeyBridge()
    bridge.triggered.connect(window.toggle_visibility)
    install_toggle_hotkey(bridge)

    if args.stdin:
        reader = StdinReader()
        reader.received.connect(window.update_state)
        reader.start()
        sys.exit(app.exec())

    lock_fd = acquire_overlay_lock()
    if lock_fd is None:
        # Another overlay already owns the socket — nothing to do.
        return

    server = OverlayServer()
    if not server.listen(str(socket_path())):
        os.close(lock_fd)
        return

    def refresh() -> None:
        window.set_active_control_sid(server.registry.active_sid)
        window.update_state(server.registry.active_state())

    def on_empty() -> None:
        def maybe_quit() -> None:
            if server.registry.count() == 0:
                app.quit()
        QTimer.singleShot(_GRACE_MS, maybe_quit)

    def on_control(action: ControlMsg) -> None:
        sid = window.active_control_sid
        if sid is not None:
            server.send_control(sid, action)

    server.state_changed.connect(refresh)
    server.empty.connect(on_empty)
    window.control_requested.connect(on_control)
    refresh()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Adjust `window.py` for the new control path** (Task 6b, same task):
  - `IslandWindow.__init__`: drop the `control_path` param; add `self.active_control_sid: str | None = None` and a Qt `Signal control_requested = Signal(object)` carrying a `ControlMsg`.
  - Add `set_active_control_sid(self, sid: str | None) -> None`.
  - `update_state(self, state: IslandState | None)`: if `state is None`, show the idle placeholder (reuse the existing `_state is None` branch in `_render`); else set `self._state = state` as before.
  - `_emit_control(self, action)`: replace the file-write body with `self.control_requested.emit(ControlMsg(action=action, request_id=self._state.request_id if self._state else None))`.
  - Import `ControlMsg` from `vibe.core.pawgress.protocol` and `Signal` from `PySide6.QtCore`.

```python
# window.py — key deltas (apply to existing class)
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from vibe.core.pawgress.protocol import ControlMsg

class IslandWindow(QWidget):
    control_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        # ...existing init, remove control_path...
        self.active_control_sid: str | None = None
        # ...

    def set_active_control_sid(self, sid: str | None) -> None:
        self.active_control_sid = sid

    def update_state(self, state: IslandState | None) -> None:
        self._state = state
        self._state_ticks = self._ticks
        self._render()
        self._resize()
        if state is not None:
            make_visible_on_all_spaces(self)

    def _emit_control(self, action: ControlAction) -> None:
        request_id = self._state.request_id if self._state is not None else None
        self.control_requested.emit(ControlMsg(action=action, request_id=request_id))
```

- [ ] **Step 3: Import + smoke.** Run: `uv run python -c "import vibe.overlay.__main__; import vibe.overlay.window"` — Expected: no error.
- [ ] **Step 4: Commit**

```bash
git add vibe/overlay/__main__.py vibe/overlay/window.py
git commit -m "feat: overlay socket server entrypoint + grace-exit + control signal"
```

---

### Task 7: app.py — use PawgressClient instead of sink/control files

**Files:**
- Modify: `vibe/cli/textual_ui/app.py`
- Delete: `vibe/cli/pawgress/sink.py`; remove `PawgressSink` from `vibe/cli/pawgress/__init__.py`; add `PawgressClient`.

**Interfaces:**
- Consumes: `PawgressClient` (Task 5), `ControlMsg` (Task 1).

- [ ] **Step 1: Swap the sink for the client.**
  - Replace the `_pawgress_sink_cache: PawgressSink | None` attr and `_pawgress_sink` property with a `_pawgress_client: PawgressClient | None = None` and an async ensure-helper:

```python
    _pawgress_client: PawgressClient | None = None

    async def _ensure_pawgress_client(self, label: str) -> PawgressClient:
        if self._pawgress_client is None:
            client = PawgressClient(on_control=self._on_pawgress_control, label=label)
            await client.connect()
            self._pawgress_client = client
        return self._pawgress_client
```

  - Every `self._pawgress_sink.write(state)` becomes `client.send_state(state)`. Every `self._pawgress_sink.reset()` is dropped (no shared file to reset). `launch_overlay(...)` calls are removed (the client launches on connect). `_show_pawgress_preparing` writes the PREPARING state via the client.
  - `_pawgress_command`: at the top of goal setup, `client = await self._ensure_pawgress_client(description)`; use `client.send_state(...)` everywhere a sink write occurred.

- [ ] **Step 2: Replace control-file polling with the client callback.**
  - Delete `_start_pawgress_control_watch`, `_poll_pawgress_control`, `_pawgress_control_path`, `_pawgress_control_pos`, `_pawgress_control_started`.
  - Add `_on_pawgress_control(self, msg: ControlMsg) -> None` that dispatches on `msg.action` exactly as the old poll loop did (pause/resume/stop/focus_vibe/allow_*/deny), using `self.call_from_thread` if needed — but since the client runs in the same asyncio loop as the app, call `self.call_later(...)` / the existing async handlers directly. Reuse the existing branch bodies verbatim (pause→`controller.pause()`, resume→`self.call_later(self._resume_pawgress_goal)`, stop→`controller.stop()`, focus→`self.call_later(focus_terminal)`, approvals→`self.call_later(self._resolve_pawgress_approval, msg.action)` gated on `request_id == self._pawgress_approval_id`).

- [ ] **Step 3: Close the client on app exit.**
  - In the app's existing shutdown/unmount path (find `async def on_unmount` or the exit hook; if none, add `on_unmount`), `if self._pawgress_client is not None: await self._pawgress_client.close()`.

- [ ] **Step 4: Import-check + full pawgress suite.**

Run: `uv run python -c "import vibe.cli.textual_ui.app"`
Run: `uv run pytest tests/pawgress -q`
Expected: import ok; tests PASS.

- [ ] **Step 5: Commit**

```bash
git add vibe/cli/textual_ui/app.py vibe/cli/pawgress/
git commit -m "refactor: drive pawgress overlay via socket client, drop file IPC"
```

---

### Task 8: Phase-1 manual verification (single + multi session)

**Files:** none (verification).

- [ ] **Step 1: Single session (regression).** `uv run vibe`, then `/pawgress make the pawgress tests pass`. Expect: overlay appears, shows planning→working, approvals work, and it dies when you quit vibe.
- [ ] **Step 2: Two sessions.** Terminal tab A: `uv run vibe` → `/pawgress goal A --verify "sleep 1"`. Terminal tab B: `uv run vibe` → `/pawgress goal B --verify "sleep 1"`. Expect: ONE overlay; it shows the most-recently-updated session; both sessions' controls work; no second window; no clobber.
- [ ] **Step 3: Lifecycle.** Quit tab A — overlay stays (B alive). Quit tab B — overlay disappears within ~2s. `kill -9` a vibe — its session drops from the overlay within the socket-close window.
- [ ] **Step 4: Demo feed still works.** `uv run python stub_feed.py | uv run python -m vibe.overlay --stdin` — Expect: the scripted island as before.

---

## PHASE 2 — Tab bar + switching

### Task 9: Tab bar in the overlay window

**Files:**
- Modify: `vibe/overlay/window.py`, `vibe/overlay/__main__.py`, `vibe/overlay/render.py`

**Interfaces:**
- Consumes: `server.registry.order()` (list of sids) and each entry's label; `set_active(sid)`.

- [ ] **Step 1: Add a `tabs_html(entries, active_sid)` renderer** in `render.py`:

```python
def tabs_html(entries: list[tuple[str, str]], active_sid: str | None) -> str:
    # entries: list of (sid, label). Renders clickable tabs "[A] [B*]" — active bolded.
    parts = []
    for sid, label in entries:
        short = (label[:14] + "…") if len(label) > 15 else label
        color = FG if sid == active_sid else MUTED
        parts.append(_button(f"[{short}]", f"tab:{sid}", color))
    return _BUTTON_GAP.join(parts)
```

- [ ] **Step 2: Window renders the tab bar** above the island when `len(entries) > 1`, and handles `tab:<sid>` links in `_on_link` by emitting a new `tab_selected = Signal(str)`.
- [ ] **Step 3: `__main__` wires** `window.tab_selected` → `server.registry.set_active(sid); refresh()`, and passes `server.registry` entries+labels into `window` on each `refresh()` via a `window.set_tabs(entries, active_sid)` method.
- [ ] **Step 4: Manual verify** two sessions show two tabs; clicking a tab switches the shown island and routes controls to that session.
- [ ] **Step 5: Commit**

```bash
git add vibe/overlay/
git commit -m "feat: overlay tab bar to switch between pawgress sessions"
```

---

## Self-Review Notes

- **Single-session unchanged:** Task 7 keeps every state write and every control action; only the transport changes (client vs file). Task 8 Step 1 is the regression gate.
- **Refcount lifecycle:** connection close (any cause, incl. `kill -9`) → `_on_disconnected` → `_drop` → `empty` → 2s grace → quit. No PID polling.
- **Race-free launch:** flock in `acquire_overlay_lock`, held for overlay lifetime; losing candidates return early in `__main__`. Clients retry-connect.
- **Type consistency:** `ControlMsg`, `HelloMsg`, `StateMsg`, `ByeMsg`, `encode_line`, `decode_line`, `SessionRegistry.active_state()/set_active()/order()/count()`, `socket_path()`, `acquire_overlay_lock()`, `spawn_overlay_candidate()`, `PawgressClient.send_state()/connect()/close()` are named identically across tasks.
- **Known non-unit-tested surface:** Qt server/window and multi-process lifecycle — covered by Task 8 manual steps (cannot be unit-tested headless).
