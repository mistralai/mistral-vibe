"""Session-scoped ownership of terminals, readers, and operation receipts."""

import asyncio
import logging
import queue
import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from mistralai_vibe_local_harness.vibe._processes._backend import (
    ManagedTerminal,
    PtyBackend,
    TerminalBackend,
    TerminalBackendError,
)
from mistralai_vibe_local_harness.vibe._processes._output import OutputPage, ProcessOutputStore

logger = logging.getLogger("vibe.unified_harness.processes")

_TERMINATION_TIMEOUT_SECONDS = 2.0
_READER_DRAIN_TIMEOUT_SECONDS = 2.0

type ProcessStatus = Literal["running", "completed", "failed", "stopped", "orphaned"]
type OperationName = Literal["start", "write", "stop"]


class ProcessManagerError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, object]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True, slots=True)
class ProcessStartRequest:
    process_id: str
    command: str
    cwd: Path
    env: dict[str, str]
    shell: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ProcessStarted:
    process_id: str
    pty_backend: PtyBackend
    started_at: str


@dataclass(frozen=True, slots=True)
class ProcessWriteResult:
    process_id: str
    status: ProcessStatus
    bytes_written: int


@dataclass(frozen=True, slots=True)
class ProcessStopResult:
    process_id: str
    status: ProcessStatus
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class ProcessTerminalSnapshot:
    process_id: str
    pty_backend: PtyBackend
    started_at: str
    finished_at: str
    status: Literal["completed", "failed", "stopped", "orphaned"]
    exit_code: int | None
    output_available: bool


@dataclass(slots=True)
class ProcessOperationReceipt:
    operation_id: str
    operation: OperationName
    request_sha256: str
    future: asyncio.Future[object]
    result: object | None = None
    error: ProcessManagerError | None = None


@dataclass(slots=True)
class _QueuedOperation:
    call: Callable[[], object]
    receipt: ProcessOperationReceipt
    process_id: str


@dataclass(slots=True)
class _ProcessEntry:
    request: ProcessStartRequest
    terminal: ManagedTerminal
    output: ProcessOutputStore
    condition: threading.Condition
    started_at: str
    reader_generation: int = 1
    reader: threading.Thread | None = None
    stopping: bool = False
    finishing: bool = False
    terminal_snapshot: ProcessTerminalSnapshot | None = None
    write_in_progress: bool = False
    close_pending: bool = False


class SessionProcessManager:
    def __init__(
        self,
        *,
        session_root: Path,
        backend: TerminalBackend,
        terminal_callback: Callable[[ProcessTerminalSnapshot], None],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.manager_instance_id = secrets.token_hex(16)
        self._session_root = session_root
        self._backend = backend
        self._terminal_callback = terminal_callback
        self._loop = loop or asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._entries: dict[str, _ProcessEntry] = {}
        self._receipts: dict[str, ProcessOperationReceipt] = {}
        self._active_writes: dict[str, ProcessOperationReceipt] = {}
        self._operations: queue.Queue[_QueuedOperation | None] = queue.Queue()
        self._terminations: queue.Queue[_QueuedOperation | None] | None = (
            queue.Queue() if backend.command_environment != "unix" else None
        )
        self._accepting = True
        self._shutdown_future: asyncio.Future[object] | None = None
        self._shutdown_sentinel_sent = False
        self._runner = threading.Thread(
            target=self._run_operations,
            name=f"process-operations-{self.manager_instance_id[:8]}",
            daemon=True,
        )
        self._runner.start()
        self._termination_runner = (
            threading.Thread(
                target=self._run_terminations,
                name=f"process-terminations-{self.manager_instance_id[:8]}",
                daemon=True,
            )
            if self._terminations is not None
            else None
        )
        if self._termination_runner is not None:
            self._termination_runner.start()

    @property
    def backend(self) -> TerminalBackend:
        return self._backend

    async def start(
        self, operation_id: str, request_sha256: str, request: ProcessStartRequest
    ) -> ProcessStarted:
        result = await self._execute_once(
            operation_id,
            "start",
            request_sha256,
            request.process_id,
            lambda: self._start(request),
        )
        return cast(ProcessStarted, result)

    async def write(
        self, operation_id: str, request_sha256: str, process_id: str, data: bytes
    ) -> ProcessWriteResult:
        result = await self._execute_once(
            operation_id,
            "write",
            request_sha256,
            process_id,
            lambda: self._write(process_id, data),
        )
        return cast(ProcessWriteResult, result)

    async def stop(
        self, operation_id: str, request_sha256: str, process_id: str
    ) -> ProcessStopResult:
        entry = self._get_entry(process_id)
        result = await self._execute_once(
            operation_id,
            "stop",
            request_sha256,
            process_id,
            lambda: self._stop(process_id),
            queue=self._terminations,
            before_enqueue=(
                (lambda: self._mark_stopping(entry)) if self._terminations is not None else None
            ),
        )
        return cast(ProcessStopResult, result)

    async def rollback_start(self, process_id: str) -> ProcessTerminalSnapshot:
        future: asyncio.Future[object] = self._loop.create_future()
        receipt = ProcessOperationReceipt(
            operation_id=f"rollback:{process_id}",
            operation="start",
            request_sha256=process_id,
            future=future,
        )
        self._operations.put(
            _QueuedOperation(
                call=lambda: self._terminate(self._get_entry(process_id), "failed"),
                receipt=receipt,
                process_id=process_id,
            )
        )
        return cast(ProcessTerminalSnapshot, await asyncio.shield(future))

    async def output(
        self,
        process_id: str,
        *,
        from_end: bool,
        cursor: int,
        wait_ms: int,
        max_bytes: int,
    ) -> tuple[OutputPage, ProcessStatus, int | None]:
        deadline = time.monotonic() + wait_ms / 1000
        while True:
            entry = self._get_entry(process_id)
            page = await asyncio.to_thread(
                entry.output.read,
                from_end=from_end,
                cursor=cursor,
                max_bytes=max_bytes,
            )
            with entry.condition:
                snapshot = entry.terminal_snapshot
            status = snapshot.status if snapshot is not None else "running"
            exit_code = snapshot.exit_code if snapshot is not None else None
            if (
                from_end
                or cursor < page.bytes_available
                or status != "running"
                or wait_ms == 0
                or time.monotonic() >= deadline
            ):
                return page, status, exit_code
            await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))

    def acknowledge(self, operation_id: str, request_sha256: str) -> None:
        with self._lock:
            receipt = self._receipts.get(operation_id)
            if receipt is None:
                return
            if receipt.request_sha256 != request_sha256:
                raise ProcessManagerError(
                    "process_identity_conflict",
                    "Process operation identity conflicts with an earlier request",
                    {"actionId": operation_id},
                )
            if not receipt.future.done():
                raise RuntimeError("cannot acknowledge an unfinished process operation")
            del self._receipts[operation_id]

    def receipt(self, operation_id: str) -> ProcessOperationReceipt | None:
        with self._lock:
            return self._receipts.get(operation_id)

    def terminal_snapshot(self, process_id: str) -> ProcessTerminalSnapshot | None:
        entry = self._get_entry(process_id)
        with entry.condition:
            return entry.terminal_snapshot

    def has_live_process(self, process_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(process_id)
        if entry is None:
            return False
        with entry.condition:
            return entry.terminal_snapshot is None

    def has_live_processes(self) -> bool:
        with self._lock:
            entries = tuple(self._entries.values())
        for entry in entries:
            with entry.condition:
                if entry.terminal_snapshot is None:
                    return True
        return False

    async def shutdown(self) -> None:
        with self._lock:
            future = self._shutdown_future
            if future is None:
                self._accepting = False
                future = self._loop.create_future()
                self._shutdown_future = future
                receipt = ProcessOperationReceipt(
                    operation_id=f"shutdown:{self.manager_instance_id}",
                    operation="stop",
                    request_sha256=self.manager_instance_id,
                    future=future,
                )
                entries = tuple(self._entries.values())
                for entry in entries:
                    with entry.condition:
                        if entry.terminal_snapshot is None:
                            entry.stopping = True
                shutdown_queue = self._terminations or self._operations
                shutdown_queue.put(
                    _QueuedOperation(
                        call=self._shutdown_all,
                        receipt=receipt,
                        process_id=self.manager_instance_id,
                    )
                )
        error: BaseException | None = None
        try:
            await asyncio.shield(future)
        except BaseException as caught:
            error = caught
        with self._lock:
            if not self._shutdown_sentinel_sent:
                self._shutdown_sentinel_sent = True
                self._operations.put(None)
                if self._terminations is not None:
                    self._terminations.put(None)
        await asyncio.to_thread(self._runner.join, 1)
        if self._termination_runner is not None:
            await asyncio.to_thread(self._termination_runner.join, 1)
        if error is not None:
            raise error

    async def _execute_once(
        self,
        operation_id: str,
        operation: OperationName,
        request_sha256: str,
        process_id: str,
        call: Callable[[], object],
        *,
        queue: queue.Queue[_QueuedOperation | None] | None = None,
        before_enqueue: Callable[[], None] | None = None,
    ) -> object:
        with self._lock:
            receipt = self._receipts.get(operation_id)
            if receipt is not None:
                if receipt.operation != operation or receipt.request_sha256 != request_sha256:
                    raise ProcessManagerError(
                        "process_identity_conflict",
                        "Process operation identity conflicts with an earlier request",
                        {"actionId": operation_id},
                    )
            else:
                if not self._accepting:
                    raise _shutdown_error(operation, process_id)
                receipt = ProcessOperationReceipt(
                    operation_id=operation_id,
                    operation=operation,
                    request_sha256=request_sha256,
                    future=self._loop.create_future(),
                )
                self._receipts[operation_id] = receipt
                if before_enqueue is not None:
                    before_enqueue()
                (queue or self._operations).put(
                    _QueuedOperation(call=call, receipt=receipt, process_id=process_id)
                )
        return await asyncio.shield(receipt.future)

    def _run_operations(self) -> None:
        while queued := self._operations.get():
            self._run_queued_operation(queued)

    def _run_terminations(self) -> None:
        if self._terminations is None:
            return
        while queued := self._terminations.get():
            self._run_queued_operation(queued)

    def _run_queued_operation(self, queued: _QueuedOperation) -> None:
        receipt = queued.receipt
        if receipt.operation == "write":
            with self._lock:
                self._active_writes[queued.process_id] = receipt
        try:
            result = queued.call()
        except ProcessManagerError as error:
            with self._lock:
                if receipt.error is None:
                    receipt.error = error
            logger.warning("background_process.operation_failed")
            self._loop.call_soon_threadsafe(_set_future_exception, receipt.future, error)
        except BaseException:
            error = ProcessManagerError(
                "process_io_failed",
                "Background process I/O failed",
                {"operation": receipt.operation},
            )
            with self._lock:
                if receipt.error is None:
                    receipt.error = error
            logger.warning("background_process.operation_failed")
            self._loop.call_soon_threadsafe(_set_future_exception, receipt.future, error)
        else:
            shutdown_error: ProcessManagerError | None = None
            with self._lock:
                if receipt.error is None:
                    if receipt.operation == "start" and not self._accepting:
                        shutdown_error = _shutdown_error("start", queued.process_id)
                        receipt.error = shutdown_error
                    else:
                        receipt.result = result
            if shutdown_error is not None:
                self._loop.call_soon_threadsafe(
                    _set_future_exception, receipt.future, shutdown_error
                )
            else:
                self._loop.call_soon_threadsafe(_set_future_result, receipt.future, result)
        finally:
            if receipt.operation == "write":
                with self._lock:
                    if self._active_writes.get(queued.process_id) is receipt:
                        del self._active_writes[queued.process_id]

    def _start(self, request: ProcessStartRequest) -> ProcessStarted:
        with self._lock:
            if not self._accepting:
                raise _shutdown_error("start", request.process_id)
            if request.process_id in self._entries:
                raise ProcessManagerError(
                    "process_identity_conflict",
                    "Process operation identity conflicts with an earlier request",
                    {"processId": request.process_id},
                )
        try:
            output = ProcessOutputStore.create(self._session_root, request.process_id)
        except OSError as error:
            raise ProcessManagerError(
                "process_start_failed",
                "Background process could not be started",
                {"processId": request.process_id, "stage": "output_initialization"},
            ) from error
        try:
            terminal = self._backend.start_terminal(
                shell=request.shell,
                command=request.command,
                cwd=request.cwd,
                env=request.env,
            )
        except (OSError, TerminalBackendError) as error:
            try:
                output.mark_unavailable()
            except Exception:
                pass
            raise ProcessManagerError(
                "process_start_failed",
                "Background process could not be started",
                {"processId": request.process_id, "stage": "spawn"},
            ) from error
        started_at = _timestamp_at_least(request.created_at)
        entry = _ProcessEntry(
            request=request,
            terminal=terminal,
            output=output,
            condition=threading.Condition(),
            started_at=started_at,
        )
        with self._lock:
            self._entries[request.process_id] = entry
            closing = not self._accepting
        if closing:
            self._terminate(entry, "failed")
            raise _shutdown_error("start", request.process_id)
        try:
            reader = threading.Thread(
                target=self._read_process,
                args=(entry, entry.reader_generation),
                name=f"process-reader-{request.process_id[-8:]}",
                daemon=True,
            )
            entry.reader = reader
            reader.start()
        except BaseException as error:
            self._terminate(entry, "failed")
            raise ProcessManagerError(
                "process_start_failed",
                "Background process could not be started",
                {"processId": request.process_id, "stage": "post_spawn"},
            ) from error
        logger.info("background_process.started")
        return ProcessStarted(request.process_id, terminal.pty_backend, started_at)

    def _write(self, process_id: str, data: bytes) -> ProcessWriteResult:
        entry = self._get_entry(process_id)
        with entry.condition:
            snapshot = entry.terminal_snapshot
            if snapshot is not None:
                raise ProcessManagerError(
                    "process_not_running",
                    "Background process is not running",
                    {"processId": process_id, "status": snapshot.status},
                )
            if entry.stopping:
                raise ProcessManagerError(
                    "process_not_running",
                    "Background process is not running",
                    {"processId": process_id, "status": "stopped"},
                )
            entry.write_in_progress = True
        try:
            written = entry.terminal.write(data)
        except (OSError, EOFError, UnicodeError) as error:
            with entry.condition:
                snapshot = entry.terminal_snapshot
            if snapshot is None and _root_is_terminal(entry.terminal):
                snapshot = self._finish(entry, "completed")
            if snapshot is not None:
                raise ProcessManagerError(
                    "process_not_running",
                    "Background process is not running",
                    {"processId": process_id, "status": snapshot.status},
                ) from error
            raise ProcessManagerError(
                "process_io_failed",
                "Background process I/O failed",
                {"processId": process_id, "operation": "write"},
            ) from error
        finally:
            close_terminal = False
            with entry.condition:
                entry.write_in_progress = False
                if entry.close_pending:
                    entry.close_pending = False
                    close_terminal = True
            if close_terminal:
                self._close_terminal(entry)
        return ProcessWriteResult(process_id, "running", written)

    def _stop(self, process_id: str) -> ProcessStopResult:
        entry = self._get_entry(process_id)
        if _root_exit_code(entry.terminal) is not None:
            snapshot = self._finish(entry, "completed")
            return ProcessStopResult(process_id, snapshot.status, snapshot.exit_code)
        with entry.condition:
            if entry.terminal_snapshot is not None:
                snapshot = entry.terminal_snapshot
                return ProcessStopResult(process_id, snapshot.status, snapshot.exit_code)
            if entry.finishing:
                while entry.terminal_snapshot is None:
                    entry.condition.wait()
                snapshot = entry.terminal_snapshot
                return ProcessStopResult(process_id, snapshot.status, snapshot.exit_code)
            entry.stopping = True
        try:
            self._backend.request_termination(entry.terminal)
            try:
                entry.terminal.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self._backend.force_termination(entry.terminal)
                entry.terminal.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired, TerminalBackendError):
            if not _root_is_terminal(entry.terminal):
                with entry.condition:
                    entry.stopping = False
                raise ProcessManagerError(
                    "process_stop_failed",
                    "Background process did not reach a terminal PTY-root state",
                    {"processId": process_id, "status": "running", "exitCode": None},
                ) from None
        if self._terminations is not None and _root_is_terminal(entry.terminal):
            self._fail_active_write(process_id)
        if entry.reader is not None:
            entry.reader.join(timeout=_READER_DRAIN_TIMEOUT_SECONDS)
            if entry.reader.is_alive():
                self._abandon_reader(entry)
        with entry.condition:
            snapshot = entry.terminal_snapshot
        if snapshot is None:
            snapshot = self._finish(
                entry, "stopped" if _root_is_terminal(entry.terminal) else "orphaned"
            )
        return ProcessStopResult(process_id, snapshot.status, snapshot.exit_code)

    def _shutdown_all(self) -> None:
        with self._lock:
            entries = [self._entries[process_id] for process_id in sorted(self._entries)]
        if self._backend.command_environment == "unix":
            self._shutdown_posix(entries)
            return
        for entry in entries:
            process_id = entry.request.process_id
            if not self.has_live_process(process_id):
                continue
            try:
                self._stop(process_id)
            except Exception:
                logger.warning("background_process.operation_failed")
                self._finish(entry, "orphaned")

    def _shutdown_posix(self, entries: list[_ProcessEntry]) -> None:
        live: list[_ProcessEntry] = []
        for entry in entries:
            with entry.condition:
                if entry.terminal_snapshot is not None:
                    continue
                if not entry.finishing:
                    entry.stopping = True
            live.append(entry)
            try:
                self._backend.request_termination(entry.terminal)
            except Exception:
                logger.warning("background_process.operation_failed")

        shutdown_entries = list(live)
        live = _wait_for_live_roots(live, _TERMINATION_TIMEOUT_SECONDS)
        for entry in live:
            try:
                self._backend.force_termination(entry.terminal)
            except Exception:
                logger.warning("background_process.operation_failed")
        uncontrolled = {
            id(entry) for entry in _wait_for_live_roots(live, _TERMINATION_TIMEOUT_SECONDS)
        }

        drain_deadline = time.monotonic() + _READER_DRAIN_TIMEOUT_SECONDS
        for entry in shutdown_entries:
            reader = entry.reader
            if reader is not None:
                reader.join(timeout=max(0, drain_deadline - time.monotonic()))
                if reader.is_alive():
                    self._abandon_reader(entry)
            with entry.condition:
                snapshot = entry.terminal_snapshot
            if snapshot is None:
                self._finish(
                    entry,
                    "orphaned"
                    if id(entry) in uncontrolled
                    else ("stopped" if _root_is_terminal(entry.terminal) else "orphaned"),
                )

    def _read_process(self, entry: _ProcessEntry, generation: int) -> None:
        status: Literal["completed", "failed"] = "completed"
        drain_deadline: float | None = None
        try:
            while True:
                if entry.terminal.wait_readable(0.1):
                    chunk = entry.terminal.read(64 * 1024)
                    if chunk:
                        entry.output.append(chunk)
                        continue
                    if entry.terminal.poll() is not None:
                        break
                if entry.terminal.poll() is None:
                    continue
                if drain_deadline is None:
                    drain_deadline = time.monotonic() + _READER_DRAIN_TIMEOUT_SECONDS
                if time.monotonic() >= drain_deadline:
                    entry.output.mark_unavailable()
                    break
        except Exception:
            status = "failed"
            try:
                entry.output.mark_unavailable()
            except Exception:
                pass
        with entry.condition:
            if generation != entry.reader_generation or entry.terminal_snapshot is not None:
                return
        if status == "failed" and not _root_is_terminal(entry.terminal):
            try:
                self._backend.force_termination(entry.terminal)
                entry.terminal.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
            except Exception:
                self._finish(entry, "orphaned", reader_generation=generation)
                return
        self._finish(entry, status, reader_generation=generation)

    def _terminate(
        self, entry: _ProcessEntry, cause: Literal["failed", "stopped"]
    ) -> ProcessTerminalSnapshot:
        with entry.condition:
            if entry.terminal_snapshot is not None:
                return entry.terminal_snapshot
        if _root_is_terminal(entry.terminal):
            return self._finish(entry, "completed")
        try:
            self._backend.request_termination(entry.terminal)
            entry.terminal.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
        except Exception:
            try:
                self._backend.force_termination(entry.terminal)
                entry.terminal.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
            except Exception:
                return self._finish(entry, "orphaned")
        return self._finish(entry, cause)

    def _finish(
        self,
        entry: _ProcessEntry,
        status: Literal["completed", "failed", "stopped", "orphaned"],
        *,
        reader_generation: int | None = None,
    ) -> ProcessTerminalSnapshot:
        with entry.condition:
            if entry.terminal_snapshot is not None:
                return entry.terminal_snapshot
            if reader_generation is not None and reader_generation != entry.reader_generation:
                while entry.terminal_snapshot is None:
                    entry.condition.wait()
                return entry.terminal_snapshot
            if entry.finishing:
                while entry.terminal_snapshot is None:
                    entry.condition.wait()
                return entry.terminal_snapshot
            entry.finishing = True
            if reader_generation is not None and entry.stopping:
                status = "stopped"
        output_available = entry.output.state.output_available
        try:
            if status == "orphaned":
                entry.output.mark_unavailable()
                output_available = False
            else:
                output_available = entry.output.finalize().output_available
        except Exception:
            output_available = False
            status = "failed" if _root_is_terminal(entry.terminal) else "orphaned"
        exit_code = _root_exit_code(entry.terminal) if status == "completed" else None
        if status == "completed" and exit_code is None:
            status = "orphaned"
            try:
                entry.output.mark_unavailable()
            except Exception:
                pass
            output_available = False
        snapshot = ProcessTerminalSnapshot(
            process_id=entry.request.process_id,
            pty_backend=entry.terminal.pty_backend,
            started_at=entry.started_at,
            finished_at=_timestamp_at_least(entry.started_at),
            status=status,
            exit_code=exit_code,
            output_available=output_available,
        )
        with entry.condition:
            entry.terminal_snapshot = snapshot
            entry.condition.notify_all()
        self._close_terminal(entry)
        self._publish_terminal(snapshot)
        return snapshot

    def _mark_stopping(self, entry: _ProcessEntry) -> None:
        with entry.condition:
            if entry.terminal_snapshot is None:
                entry.stopping = True

    def _abandon_reader(self, entry: _ProcessEntry) -> None:
        with entry.condition:
            entry.reader_generation += 1
        self._close_terminal(entry)
        try:
            entry.output.mark_unavailable()
        except Exception:
            pass

    def _fail_active_write(self, process_id: str) -> None:
        with self._lock:
            receipt = self._active_writes.get(process_id)
            if receipt is None or receipt.error is not None:
                return
            error = ProcessManagerError(
                "process_io_failed",
                "Background process I/O failed",
                {"processId": process_id, "operation": "write"},
            )
            receipt.error = error
        self._loop.call_soon_threadsafe(_set_future_exception, receipt.future, error)

    def _close_terminal(self, entry: _ProcessEntry) -> None:
        with entry.condition:
            if entry.write_in_progress:
                entry.close_pending = True
                return
        try:
            entry.terminal.close()
        except Exception:
            logger.warning("background_process.operation_failed")

    def _publish_terminal(self, snapshot: ProcessTerminalSnapshot) -> None:
        self._loop.call_soon_threadsafe(self._terminal_callback, snapshot)

    def _get_entry(self, process_id: str) -> _ProcessEntry:
        with self._lock:
            entry = self._entries.get(process_id)
        if entry is None:
            raise ProcessManagerError(
                "unknown_process",
                "Unknown background process",
                {"processId": process_id},
            )
        return entry


def _set_future_result(future: asyncio.Future[object], result: object) -> None:
    if not future.done():
        future.set_result(result)


def _set_future_exception(future: asyncio.Future[object], error: ProcessManagerError) -> None:
    if not future.done():
        future.set_exception(error)


def _timestamp_at_least(minimum: str) -> str:
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return max(now, minimum)


def _wait_for_live_roots(
    entries: list[_ProcessEntry], timeout_seconds: float
) -> list[_ProcessEntry]:
    deadline = time.monotonic() + timeout_seconds
    live = entries
    while live and time.monotonic() < deadline:
        live = [entry for entry in live if not _root_is_terminal(entry.terminal)]
        if live:
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    return [entry for entry in live if not _root_is_terminal(entry.terminal)]


def _root_is_terminal(terminal: ManagedTerminal) -> bool:
    return _root_exit_code(terminal) is not None


def _root_exit_code(terminal: ManagedTerminal) -> int | None:
    try:
        return terminal.poll()
    except Exception:
        return None


def _shutdown_error(operation: OperationName, process_id: str) -> ProcessManagerError:
    if operation == "start":
        return ProcessManagerError(
            "process_start_failed",
            "Background process could not be started",
            {"processId": process_id, "stage": "shutdown"},
        )
    return ProcessManagerError(
        "process_io_failed",
        "Background process I/O failed",
        {"operation": operation, "processId": process_id, "stage": "shutdown"},
    )


__all__ = [
    "ProcessManagerError",
    "ProcessOperationReceipt",
    "ProcessStartRequest",
    "ProcessStarted",
    "ProcessStopResult",
    "ProcessTerminalSnapshot",
    "ProcessWriteResult",
    "SessionProcessManager",
]
