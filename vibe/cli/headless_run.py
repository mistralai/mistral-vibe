from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import signal
import sys
import time
from types import FrameType
from typing import NoReturn

from vibe import __version__
from vibe.app_server.models import AgentStatsSnapshot
from vibe.app_server.run_export import (
    RunError,
    RunExport,
    RunLimits,
    RunOutcome,
    RunResult,
    copy_journal,
    write_run_export,
)
from vibe.observability.logging import logger

# How long a run may take to wind down once it ends or is stopped: interrupting
# the turn, letting it settle, reading usage and closing the session, together.
STOP_GRACE_SECONDS = 10.0


# The signals that stop a headless run as `terminated`: SIGTERM from a job
# runner or `timeout`, and SIGINT from Ctrl-C.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class RunTerminated(BaseException):
    """A stop signal arrived before the run was listening for it.

    A ``BaseException``, like ``KeyboardInterrupt``, so that no ``except
    Exception`` on the way out swallows it.
    """


class StopRequests:
    """Requests to stop a headless run early: SIGTERM or SIGINT, the time limit,
    the token and price budgets, and an abort from the agent socket's server.

    Before :meth:`wait` listens, a stop signal raises :class:`RunTerminated` in
    the main thread. While it listens, any request ends the wait. After it, a
    late request is only recorded, so it cannot cut the export short. The
    deadline counts from construction, which is process start. The budgets
    count from :meth:`count_usage_from`, so a resumed session's earlier runs
    do not spend this run's budget.
    """

    def __init__(self, limits: RunLimits) -> None:
        self.limits = limits
        self._deadline = (
            time.monotonic() + limits.time_limit_s
            if limits.time_limit_s is not None
            else None
        )
        self.requested: RunOutcome | None = None
        # Why the run was aborted, when ``requested`` is ``aborted``.
        self.abort_error: RunError | None = None
        # The session's usage as the run started; none counts as nothing.
        self._usage_baseline: AgentStatsSnapshot | None = None
        self._listened = False
        self._wake: Callable[[], object] | None = None

    @contextmanager
    def handling_stop_signals(self) -> Iterator[None]:
        previous = {
            number: signal.signal(number, self._on_stop_signal)
            for number in STOP_SIGNALS
        }
        try:
            yield
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)

    async def wait(self) -> RunOutcome:
        self._listened = True
        loop = asyncio.get_running_loop()
        requested = asyncio.Event()
        self._wake = lambda: loop.call_soon_threadsafe(requested.set)
        if self.requested is not None:
            requested.set()
        timer = (
            loop.call_later(
                max(0.0, self._deadline - time.monotonic()),
                self._request,
                RunOutcome.DEADLINE,
            )
            if self._deadline is not None
            else None
        )
        try:
            await requested.wait()
        finally:
            if timer is not None:
                timer.cancel()
            self._wake = None
        assert self.requested is not None
        return self.requested

    def count_usage_from(self, stats: AgentStatsSnapshot) -> None:
        """Take the session's usage as the run starts as its baseline.

        A resumed session's stats carry its earlier runs' usage, which this
        run neither spent nor has a budget for.
        """
        self._usage_baseline = stats

    def run_usage(self, stats: AgentStatsSnapshot) -> AgentStatsSnapshot:
        """The session's stats, with its usage counted from the baseline."""
        baseline = self._usage_baseline
        if baseline is None:
            return stats
        return stats.model_copy(
            update={
                "session_prompt_tokens": max(
                    0, stats.session_prompt_tokens - baseline.session_prompt_tokens
                ),
                "session_completion_tokens": max(
                    0,
                    stats.session_completion_tokens
                    - baseline.session_completion_tokens,
                ),
                "session_cached_tokens": max(
                    0, stats.session_cached_tokens - baseline.session_cached_tokens
                ),
            }
        )

    def observe_usage(self, stats: AgentStatsSnapshot) -> None:
        """Stop the run once it has spent more than a budget allows.

        ``stats`` are the session's; the run's usage counts from the baseline.
        """
        stats = self.run_usage(stats)
        limits = self.limits
        if (
            limits.max_tokens is not None
            and stats.session_total_llm_tokens > limits.max_tokens
        ):
            self._request(RunOutcome.TOKEN_LIMIT)
        elif limits.max_price is not None and stats.session_cost > limits.max_price:
            self._request(RunOutcome.PRICE_LIMIT)

    def abort(self, message: str) -> None:
        """Stop the run because the agent socket's server aborted it.

        Safe to call from any thread. ``message`` is the server's, kept for
        the export.
        """
        if self.requested is not None:
            return
        self.abort_error = RunError(message=message)
        self._request(RunOutcome.ABORTED)

    def _on_stop_signal(self, _signum: int, _frame: FrameType | None) -> None:
        if not self._listened:
            raise RunTerminated
        self._request(RunOutcome.TERMINATED)

    def _request(self, outcome: RunOutcome) -> None:
        if self.requested is not None:
            return
        self.requested = outcome
        if self._wake is not None:
            self._wake()


@dataclass(frozen=True, slots=True)
class RunReport:
    result: RunResult
    final_response: str | None = None
    session_dir: Path | None = None


@dataclass(slots=True)
class HeadlessRun:
    """One `vibe -p` invocation: ends the process with its outcome's exit code
    after writing the export, whichever path it leaves by.
    """

    output_dir: Path | None
    stop: StopRequests
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def for_args(cls, args: argparse.Namespace) -> HeadlessRun | None:
        """The headless run the arguments ask for, or None for interactive mode.

        Built as soon as the arguments are parsed, so every later failure can
        leave an export and the time limit counts from process start.
        """
        if args.prompt is None and args.prompt_file is None:
            return None
        limits = RunLimits(
            max_turns=args.max_turns,
            max_price=args.max_price,
            max_tokens=args.max_tokens,
            time_limit_s=args.time_limit,
        )
        return cls(output_dir=args.output_dir, stop=StopRequests(limits))

    def fail(self, outcome: RunOutcome, message: str) -> NoReturn:
        print(f"Error: {message}", file=sys.stderr)
        self.exit(
            RunReport(
                result=RunResult(outcome=outcome, error=RunError(message=message))
            )
        )

    def exit(self, report: RunReport) -> NoReturn:
        if self.output_dir is not None:
            self._write(self.output_dir, report)
        sys.exit(report.result.outcome.exit_code)

    def _write(self, output_dir: Path, report: RunReport) -> None:
        journal_dir: str | None = None
        if report.session_dir is not None and report.session_dir.is_dir():
            try:
                journal_dir = copy_journal(report.session_dir, output_dir)
            except OSError:
                logger.warning("Could not copy the session journal", exc_info=True)
        export = RunExport(
            **dict(report.result),
            vibe_version=__version__,
            journal_dir=journal_dir,
            started_at=self.started_at,
            ended_at=datetime.now(UTC),
            limits=self.stop.limits,
        )
        try:
            write_run_export(output_dir, export)
        except OSError as exc:
            print(f"Error: could not write the run export: {exc}", file=sys.stderr)
