"""The app server's background worker: one child process, started on first use.

Background work (worktree reaps and retention first; PR discovery, git status
and other walks over every session next) runs Python for seconds at a time.
On a thread of the app server it would hold the GIL that every session of a
multiplexed process shares, and freeze them all while it runs; a process of
its own does not.

Callers name a job and get a future. Nothing here blocks the event loop: a
writer thread starts the process (and restarts it once it died) and sends the
requests, a reader thread resolves the futures. The worker side is
``_background_worker_process.py``; a job set is a ``module:function`` it
imports, so adding jobs is adding a function returning ``{name: Job}`` and
registering it with ``add_jobs`` before the first submit.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, field
import itertools
import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import IO, Any

from vibe.app_server._background_worker_process import FLAG
from vibe.core.paths import VIBE_HOME
from vibe.observability.logging import logger

_CLOSE_TIMEOUT_S = 2.0
# Past this many deaths in the window, wait for the next job to start it again.
_MAX_RECOVERIES = 3
_RECOVERY_WINDOW_S = 60.0


class BackgroundWorkerError(Exception): ...


class BackgroundWorkerClosedError(BackgroundWorkerError): ...


class BackgroundWorkerDiedError(BackgroundWorkerError): ...


class BackgroundJobError(BackgroundWorkerError):
    def __init__(self, job: str, error_type: str, message: str) -> None:
        super().__init__(f"Background job {job} failed: {error_type}: {message}")
        self.error_type = error_type


@dataclass(slots=True)
class _Pending:
    job: str
    future: Future[Any]
    # The process it was sent to: that process's death fails it.
    generation: int | None = None


@dataclass(frozen=True, slots=True)
class _Request:
    id: int
    line: bytes


@dataclass(slots=True)
class _Running:
    process: subprocess.Popen[bytes]
    generation: int
    stdin: IO[bytes] = field(init=False)

    def __post_init__(self) -> None:
        assert self.process.stdin is not None
        self.stdin = self.process.stdin


class BackgroundWorker:
    def __init__(self, *, command: Sequence[str] | None = None) -> None:
        self._command = None if command is None else tuple(command)
        self._job_sets: list[str] = []
        self._recoveries: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._outbox: queue.SimpleQueue[_Request | None] = queue.SimpleQueue()
        self._pending: dict[int, _Pending] = {}
        self._ids = itertools.count(1)
        self._writer: threading.Thread | None = None
        self._running: _Running | None = None
        self._generation = 0
        self._deaths: deque[float] = deque(maxlen=_MAX_RECOVERIES)
        self._closed = False

    def add_jobs(
        self, job_set: str, *, after_restart: Callable[[], None] | None = None
    ) -> None:
        """Serve the jobs ``job_set`` (``module:function``) returns.

        ``after_restart`` runs once the worker died with work in it, to queue
        that work again; whatever a job kept in memory went with the process.
        """
        with self._lock:
            if self._writer is not None:
                raise RuntimeError("Jobs must be added before the worker starts")
            self._job_sets.append(job_set)
            if after_restart is not None:
                self._recoveries.append(after_restart)

    @property
    def pid(self) -> int | None:
        with self._lock:
            return None if self._running is None else self._running.process.pid

    def submit(self, job: str, args: Any = None) -> Future[Any]:
        """Never blocks: callable from the event loop and from any thread."""
        future: Future[Any] = Future()
        request_id = next(self._ids)
        try:
            line = _encode({"id": request_id, "job": job, "args": args})
        except (TypeError, ValueError) as exc:
            future.set_exception(BackgroundJobError(job, type(exc).__name__, str(exc)))
            return future
        with self._lock:
            if self._closed:
                future.set_exception(BackgroundWorkerClosedError())
                return future
            self._pending[request_id] = _Pending(job, future)
            if self._writer is None:
                self._writer = threading.Thread(
                    target=self._write_loop, name="vibe-background-writer", daemon=True
                )
                self._writer.start()
        self._outbox.put(_Request(request_id, line))
        return future

    async def run(self, job: str, args: Any = None) -> Any:
        return await asyncio.wrap_future(self.submit(job, args))

    def post(self, job: str, args: Any = None) -> None:
        self.submit(job, args).add_done_callback(_log_unattended_failure)

    async def close(self) -> None:
        await asyncio.to_thread(self.close_now)

    def close_now(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            running = self._running
            self._running = None
            pending = list(self._pending.values())
            self._pending.clear()
        self._outbox.put(None)
        for entry in pending:
            _fail(entry.future, BackgroundWorkerClosedError())
        if running is None:
            return
        process = running.process
        try:
            running.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=_CLOSE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _write_loop(self) -> None:
        while (request := self._outbox.get()) is not None:
            line = request.line
            while True:
                try:
                    running = self._current()
                except OSError as exc:
                    with self._lock:
                        entry = self._pending.pop(request.id, None)
                    if entry is not None:
                        _fail(entry.future, exc)
                    logger.warning("Could not start the background worker: %s", exc)
                    break
                if running is None:
                    break
                with self._lock:
                    entry = self._pending.get(request.id)
                    if entry is None:
                        break
                    # A process that died since: send to the next one.
                    if self._running is not running:
                        continue
                    entry.generation = running.generation
                try:
                    running.stdin.write(line)
                    running.stdin.flush()
                except (OSError, ValueError):
                    # The reader sees the process end and fails the request.
                    pass
                break

    def _current(self) -> _Running | None:
        with self._lock:
            if self._closed:
                return None
            if self._running is not None:
                return self._running
            job_sets = tuple(self._job_sets)
        process = self._spawn(job_sets)
        with self._lock:
            running = None
            if not self._closed:
                self._generation += 1
                running = _Running(process, self._generation)
                self._running = running
        if running is None:
            process.kill()
            process.wait()
            return None
        threading.Thread(
            target=self._read_loop,
            args=(running,),
            name="vibe-background-reader",
            daemon=True,
        ).start()
        return running

    def _spawn(self, job_sets: Sequence[str]) -> subprocess.Popen[bytes]:
        command = [*(self._command or _default_command()), *job_sets]
        home = VIBE_HOME.path
        home.mkdir(parents=True, exist_ok=True)
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # The worker reads VIBE_HOME as this process does, and stands
            # outside every worktree: on Windows a working directory cannot be
            # deleted.
            env={**os.environ, "VIBE_HOME": str(home)},
            cwd=home,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _read_loop(self, running: _Running) -> None:
        stdout = running.process.stdout
        assert stdout is not None
        for line in stdout:
            try:
                message = json.loads(line)
            except ValueError:
                message = None
            if not isinstance(message, dict):
                logger.warning(
                    "Ignoring a malformed background worker answer: %r", line
                )
                continue
            request_id: Any = message.get("id")
            with self._lock:
                entry = self._pending.pop(request_id, None)
            if entry is None:
                continue
            if (error := message.get("error")) is not None:
                _fail(
                    entry.future,
                    BackgroundJobError(entry.job, error["type"], error["message"]),
                )
            else:
                _resolve(entry.future, message.get("result"))
        returncode = running.process.wait()
        with self._lock:
            if self._running is running:
                self._running = None
            lost = [
                self._pending.pop(request_id)
                for request_id, entry in tuple(self._pending.items())
                if entry.generation == running.generation
            ]
            closing = self._closed
            recover = not closing and self._may_recover()
            recoveries = tuple(self._recoveries)
        for entry in lost:
            _fail(entry.future, BackgroundWorkerDiedError(returncode))
        if closing:
            return
        logger.warning(
            "Background worker %s exited with %s", running.process.pid, returncode
        )
        if not recover:
            return
        for recovery in recoveries:
            try:
                recovery()
            # Broad: one job set's recovery must not stop the others'.
            except Exception as exc:
                logger.warning("Background worker recovery failed", exc_info=exc)

    def _may_recover(self) -> bool:
        now = time.monotonic()
        self._deaths.append(now)
        return not (
            len(self._deaths) == _MAX_RECOVERIES
            and now - self._deaths[0] < _RECOVERY_WINDOW_S
        )


def _default_command() -> list[str]:
    # A frozen build has no interpreter to run a module with: it runs itself,
    # and its entry point serves the worker on FLAG.
    if getattr(sys, "frozen", False):
        return [sys.executable, FLAG]
    return [
        sys.executable,
        "-P",
        "-c",
        "from vibe.app_server._background_worker_process import main; main()",
    ]


def _resolve(future: Future[Any], result: Any) -> None:
    try:
        future.set_result(result)
    except InvalidStateError:
        pass


def _encode(message: dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


def _fail(future: Future[Any], error: BaseException) -> None:
    try:
        future.set_exception(error)
    except InvalidStateError:
        pass


def _log_unattended_failure(future: Future[Any]) -> None:
    if future.cancelled():
        return
    error = future.exception()
    if error is None or isinstance(error, BackgroundWorkerClosedError):
        return
    logger.warning("Background job failed", exc_info=error)


__all__ = [
    "BackgroundJobError",
    "BackgroundWorker",
    "BackgroundWorkerClosedError",
    "BackgroundWorkerDiedError",
    "BackgroundWorkerError",
]
