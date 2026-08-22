"""stdio JSON-lines transport to the Accordion sidecar.

One process, two pipes, one reader thread. Everything here is defensive on
purpose: a dead or slow sidecar must degrade to "no sidecar", never to an
exception on the agent's model-call path.

See ``docs/sidecar-protocol.md`` in the Accordion repo for the wire contract.
"""

from __future__ import annotations

from collections.abc import Callable
import itertools
import json
from pathlib import Path
import queue
import subprocess
import threading
from typing import Any

from accordion_vibe._config import child_environment
from vibe.observability.logging import logger

__all__ = ["SidecarClient"]

_PROTOCOL_V = 1
_READY_TIMEOUT_S = 5.0
_SHUTDOWN_GRACE_S = 2.0
# The sidecar caps an inbound line at 64 MB and drops anything longer. Refusing
# to write it here turns a silent stall into a passthrough plus a log line.
_MAX_LINE_BYTES = 64 * 1024 * 1024
# Replies the harness correlates back to a pending request by ``req``.
_REPLY_TYPES = frozenset({"hook_result", "tool_result", "command_result"})


class SidecarClient:
    """A spawned sidecar process plus the request/reply plumbing around it."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        log_path: Path | None = None,
        on_notify: Callable[[str, str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_folding: Callable[[bool], None] | None = None,
        on_append_entry: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._log_path = log_path
        self._on_notify = on_notify
        self._on_status = on_status
        self._on_folding = on_folding
        self._on_append_entry = on_append_entry

        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._pending: dict[str, queue.SimpleQueue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._ready = threading.Event()
        self._ready_payload: dict[str, Any] = {}
        self._counter = itertools.count(1)

        self.detached_reason: str | None = None
        self.timeouts = 0
        self.send_failures = 0

    # -- lifecycle ---------------------------------------------------------

    @property
    def ready_payload(self) -> dict[str, Any]:
        return self._ready_payload

    @property
    def alive(self) -> bool:
        process = self._process
        return (
            self.detached_reason is None
            and process is not None
            and process.poll() is None
        )

    def start(self, hello: dict[str, Any], timeout: float = _READY_TIMEOUT_S) -> bool:
        """Spawn the sidecar, send ``hello`` and block until ``ready``.

        Returns False (and marks the client detached) on any failure; the
        caller then behaves exactly like upstream vibe.
        """
        try:
            self._spawn()
        except OSError as exc:  # spawn failure: node missing, bad path, ...
            self._detach(f"spawn failed: {exc}")
            return False

        self.send({"type": "hello", "v": _PROTOCOL_V, **hello})
        if not self._ready.wait(timeout):
            self._detach("no ready message within timeout")
            self.close()
            return False
        return self.alive

    def _spawn(self) -> None:
        if self._log_path is not None:
            self._log_handle = self._log_path.open("ab", buffering=0)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            self._command,
            cwd=str(self._cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log_handle or subprocess.DEVNULL,
            env=child_environment(),
            creationflags=creationflags,
        )
        self._reader = threading.Thread(
            target=self._read_loop, name="accordion-sidecar-reader", daemon=True
        )
        self._reader.start()

    def close(self, grace: float = _SHUTDOWN_GRACE_S) -> None:
        """Ask the sidecar to exit, then make sure it did."""
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            self.send({"type": "shutdown"})
            with self._write_lock:
                stdin = process.stdin
                if stdin is not None and not stdin.closed:
                    try:
                        stdin.close()
                    except OSError:
                        pass
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                process.kill()
        self._process = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            finally:
                self._log_handle = None
        self._fail_all_pending()

    # -- writing -----------------------------------------------------------

    def send(self, payload: dict[str, Any]) -> None:
        """Fire-and-forget one message. Never raises."""
        process = self._process
        if process is None or self.detached_reason is not None:
            return
        try:
            line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
            data = line.encode("utf-8")
        except (TypeError, ValueError) as exc:
            logger.debug("accordion: unserializable sidecar payload: %s", exc)
            return
        if len(data) > _MAX_LINE_BYTES:
            self.send_failures += 1
            logger.warning(
                "accordion: dropping a %d-byte %s message (over the sidecar's cap)",
                len(data),
                payload.get("type"),
            )
            return
        with self._write_lock:
            stdin = process.stdin
            if stdin is None or stdin.closed:
                return
            try:
                stdin.write(data)
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self.send_failures += 1
                self._detach(f"write failed: {exc}")

    def request(self, payload: dict[str, Any], timeout: float) -> dict[str, Any] | None:
        """Send a request and block for its reply. None on timeout or death."""
        if not self.alive:
            return None
        req = f"r{next(self._counter)}"
        inbox: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        with self._pending_lock:
            self._pending[req] = inbox
        try:
            self.send({**payload, "req": req})
            if self.detached_reason is not None:
                return None
            try:
                reply = inbox.get(timeout=timeout)
            except queue.Empty:
                self.timeouts += 1
                return None
            return reply or None
        finally:
            with self._pending_lock:
                self._pending.pop(req, None)

    # -- reading -----------------------------------------------------------

    def _read_loop(self) -> None:
        process = self._process
        stdout = process.stdout if process is not None else None
        if stdout is None:
            return
        try:
            for raw in stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    logger.debug("accordion: non-JSON sidecar line: %.200s", line)
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        except (OSError, ValueError) as exc:
            logger.debug("accordion: sidecar reader stopped: %s", exc)
        finally:
            self._detach("sidecar stdout closed")
            self._fail_all_pending()

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind in _REPLY_TYPES:
            req = message.get("req")
            if not isinstance(req, str):
                return
            with self._pending_lock:
                inbox = self._pending.get(req)
            if inbox is not None:
                inbox.put(message)
            return
        if kind == "ready":
            self._ready_payload = message
            self._ready.set()
            return
        self._dispatch_notification(kind, message)

    def _dispatch_notification(self, kind: Any, message: dict[str, Any]) -> None:
        try:
            match kind:
                case "notify" if self._on_notify is not None:
                    text = str(message.get("message", ""))
                    self._on_notify(text, str(message.get("level", "info")))
                case "status" if self._on_status is not None:
                    self._on_status(str(message.get("text", "")))
                case "folding" if self._on_folding is not None:
                    self._on_folding(bool(message.get("enabled")))
                case "append_entry" if self._on_append_entry is not None:
                    entry = message.get("entry")
                    if isinstance(entry, dict):
                        self._on_append_entry(entry)
                case _:
                    logger.debug("accordion: ignoring sidecar message %r", kind)
        except Exception as exc:  # a listener must never kill the reader
            logger.debug("accordion: sidecar listener raised: %s", exc)

    # -- teardown helpers --------------------------------------------------

    def _detach(self, reason: str) -> None:
        if self.detached_reason is None:
            self.detached_reason = reason
            logger.info("accordion: sidecar detached (%s)", reason)
        self._ready.set()

    def _fail_all_pending(self) -> None:
        with self._pending_lock:
            inboxes = list(self._pending.values())
            self._pending.clear()
        for inbox in inboxes:
            inbox.put({})
