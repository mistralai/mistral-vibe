# Twin: mistralai.vibe.harness.runtime.completion.profile.stream_guard. Keep both copies identical below this line.
"""The stream guard: how long a streamed completion may stay silent.

The first output of a model call must arrive within `FIRST_OUTPUT_WAIT_SECONDS`.
After it, `STREAM_IDLE_SECONDS` without any data is a stall. The HTTP read timeout
sits above both, and if it fires anyway it is a stall too. Opening the stream is
retried under the provider's retry budget; a stall gets `STALL_RETRIES` retries of
its own, even when that budget is spent.

The guard only measures and classifies. Each host decides how to retry a stall.
"""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Callable
from types import TracebackType
from typing import Literal, Protocol

FIRST_OUTPUT_WAIT_SECONDS = 720.0
STREAM_IDLE_SECONDS = 300.0
# The HTTP read timeout is this much above the longer of the two guard limits, so the
# guard, which names what went silent, ends a silent stream first.
READ_TIMEOUT_MARGIN_SECONDS = 60.0
STALL_RETRIES = 1

type StallKind = Literal["first_output", "idle", "read_timeout"]
type _Phase = Literal["first_output", "idle", "held"]


class StreamStalledError(TimeoutError):
    """A model stream went silent past one of the guard's limits.

    `kind` says which: no first output, silence after output started, or an HTTP read
    timeout. `seconds` is the limit that was passed.
    """

    def __init__(self, kind: StallKind, seconds: float) -> None:
        super().__init__(stall_message(kind, seconds))
        self.kind: StallKind = kind
        self.seconds = seconds


def stall_message(kind: StallKind, seconds: float) -> str:
    """Return the user-facing message for one stall."""
    if kind == "first_output":
        return f"The model produced no output within {seconds:g} seconds"
    if kind == "idle":
        return (
            f"The model stopped producing output for {seconds:g} seconds"
            " and the call was stopped"
        )
    return (
        f"The model provider sent no data for {seconds:g} seconds"
        " and the call was stopped"
    )


def is_stall(error: BaseException) -> bool:
    """Return whether an error is a stream stall."""
    return isinstance(error, StreamStalledError)


def should_retry_stall(stall_retries_used: int) -> bool:
    """Return whether a stall gets a retry, whatever is left of the retry budget."""
    return stall_retries_used < STALL_RETRIES


def read_timeout_seconds(
    first_output_seconds: float | None = None, idle_seconds: float | None = None
) -> float:
    """Return the HTTP read timeout that sits above both guard limits."""
    first_output = (
        FIRST_OUTPUT_WAIT_SECONDS
        if first_output_seconds is None
        else first_output_seconds
    )
    idle = STREAM_IDLE_SECONDS if idle_seconds is None else idle_seconds
    return max(first_output, idle) + READ_TIMEOUT_MARGIN_SECONDS


class GuardTimeout(Protocol):
    """A timeout context that can be moved while it runs, like `asyncio.Timeout`."""

    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> bool | None: ...

    def reschedule(self, when: float | None, /) -> None: ...

    def expired(self) -> bool: ...


class StreamClock(Protocol):
    """The time source and timeout primitive of a guard; tests pass a fake one."""

    def now(self) -> float: ...

    def timeout_at(self, when: float | None, /) -> GuardTimeout: ...


class LoopClock:
    """The running event loop's clock and `asyncio.timeout_at`."""

    def now(self) -> float:
        return asyncio.get_running_loop().time()

    def timeout_at(self, when: float | None, /) -> GuardTimeout:
        return asyncio.timeout_at(when)


class StreamGuard:
    """Watch one completion stream, which may span retries and several model calls.

    The guard starts waiting for the first output. `expect_first_output()` starts that
    wait again (a new attempt or a new model call), `hold()` lifts every limit (a
    retry backoff), and output switches to the idle limit, which every item resets.
    Heartbeats keep a slow stream going that way, but never past the longer limit
    since the latest item that was not a heartbeat.
    """

    def __init__(
        self,
        first_output_seconds: float | None = None,
        idle_seconds: float | None = None,
        *,
        clock: StreamClock | None = None,
    ) -> None:
        self.first_output_seconds = (
            FIRST_OUTPUT_WAIT_SECONDS
            if first_output_seconds is None
            else first_output_seconds
        )
        self.idle_seconds = (
            STREAM_IDLE_SECONDS if idle_seconds is None else idle_seconds
        )
        self._clock = clock or LoopClock()
        self._phase: _Phase = "first_output"
        self._first_output_deadline: float | None = None
        self._last_data_at: float | None = None
        # The silence that the idle deadline now allows: the idle limit, or less of
        # the first-output wait when heartbeats alone have filled the rest.
        self._idle_silence_seconds = self.idle_seconds
        self._timeout: GuardTimeout | None = None

    @property
    def read_timeout_seconds(self) -> float:
        """The HTTP read timeout for this guard's limits."""
        return read_timeout_seconds(self.first_output_seconds, self.idle_seconds)

    @property
    def output_started(self) -> bool:
        """Whether the current model call has produced output."""
        return self._phase == "idle"

    def expect_first_output(self) -> None:
        """Start the wait for the first output of a new attempt or model call."""
        self._phase = "first_output"
        now = self._clock.now()
        self._first_output_deadline = now + self.first_output_seconds
        self._last_data_at = now
        self._reschedule()

    def hold(self) -> None:
        """Lift every limit until the next attempt or output."""
        self._phase = "held"
        self._reschedule()

    def mark_output(self) -> None:
        """Record output: from now on, silence is measured against the idle limit."""
        self._phase = "idle"
        self._last_data_at = self._clock.now()
        self._reschedule()

    def stalled(self) -> StreamStalledError:
        """The stall for the limit that applies now."""
        if self._phase == "idle":
            return StreamStalledError("idle", self._idle_silence_seconds)
        return StreamStalledError("first_output", self.first_output_seconds)

    def read_timeout_stall(self) -> StreamStalledError:
        """The stall for an HTTP read timeout."""
        return StreamStalledError("read_timeout", self.read_timeout_seconds)

    async def watch[T](
        self,
        source: AsyncIterable[T],
        *,
        starts_output: Callable[[T], bool] | None = None,
        is_heartbeat: Callable[[T], bool] | None = None,
        is_read_timeout: Callable[[BaseException], bool] | None = None,
    ) -> AsyncIterator[T]:
        """Yield the source's items; raise `StreamStalledError` when it stays silent.

        An item counts as output unless `starts_output` says otherwise, and as data
        unless `is_heartbeat` says otherwise. An error that `is_read_timeout`
        recognises is raised as a read-timeout stall.
        """
        iterator = aiter(source)
        self.expect_first_output()
        while True:
            timeout = self._clock.timeout_at(self._deadline())
            self._timeout = timeout
            try:
                async with timeout:
                    item = await anext(iterator)
            except StopAsyncIteration:
                return
            except Exception as error:
                if timeout.expired():
                    raise self.stalled() from error
                if is_read_timeout is not None and is_read_timeout(error):
                    raise self.read_timeout_stall() from error
                raise
            finally:
                self._timeout = None
            if is_heartbeat is None or not is_heartbeat(item):
                self._last_data_at = self._clock.now()
            if starts_output is None or starts_output(item):
                self.mark_output()
            yield item

    def _deadline(self) -> float | None:
        if self._phase == "first_output":
            return self._first_output_deadline
        if self._phase == "idle":
            now = self._clock.now()
            last_data_at = now if self._last_data_at is None else self._last_data_at
            longest_silence = max(self.first_output_seconds, self.idle_seconds)
            data_deadline = last_data_at + longest_silence
            if now + self.idle_seconds <= data_deadline:
                self._idle_silence_seconds = self.idle_seconds
                return now + self.idle_seconds
            self._idle_silence_seconds = longest_silence
            return data_deadline
        return None

    def _reschedule(self) -> None:
        # A timeout that already fired stays fired: the stall is on its way.
        if self._timeout is not None and not self._timeout.expired():
            self._timeout.reschedule(self._deadline())
