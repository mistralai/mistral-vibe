from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import aclosing, suppress
from dataclasses import dataclass
from enum import StrEnum, auto
import json
from pathlib import Path
import sys
from typing import TextIO

from pydantic import BaseModel, JsonValue

from vibe.app_server.events import (
    AppServerEvent,
    CallbackRequested,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    StatsUpdated,
)
from vibe.app_server.local import LocalHarness, LocalHarnessHost, LocalHarnessOptions
from vibe.app_server.models import (
    AgentStatsSnapshot,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicTurn,
    TeleportCheckingGit,
    TeleportComplete,
    TeleportEvent,
    TeleportFailed,
    TeleportPushing,
    TeleportPushRequired,
    TeleportStartingWorkflow,
    TeleportSummarizingContext,
    TurnErrorCode,
    TurnStop,
    turn_stop,
)
from vibe.app_server.protocol import AppServerResponseError, ProtocolErrorCode
from vibe.app_server.run_export import (
    RunError,
    RunOutcome,
    RunResult,
    RunUsage,
    RunWarning,
)
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.cli.headless_run import STOP_GRACE_SECONDS, RunReport, StopRequests
from vibe.observability.logging import logger


class OutputFormat(StrEnum):
    TEXT = auto()
    JSON = auto()
    STREAMING = auto()


class ProgrammaticTeleportError(RuntimeError):
    pass


# App-server errors that mean the run asked for something the server cannot do.
_USAGE_ERROR_CODES = frozenset({
    ProtocolErrorCode.INVALID_PARAMS,
    ProtocolErrorCode.NOT_FOUND,
})


def is_usage_error(exc: Exception) -> bool:
    """Whether a failed programmatic run is reported as a usage error."""
    match exc:
        case ProgrammaticTeleportError():
            return True
        case AppServerResponseError(error=error):
            return error.code in _USAGE_ERROR_CODES
        case _:
            return False


class ProgrammaticOutput:
    """What a run writes to stdout.

    Text and streaming output cover this run only: a resumed session's earlier
    runs already wrote theirs. JSON output is the whole session's history.
    """

    def __init__(
        self, output_format: OutputFormat, stream: TextIO | None = None
    ) -> None:
        self._format = output_format
        self._stream = stream or sys.stdout
        self._emitted: set[str] = set()
        self._teleport_url: str | None = None
        self._earlier_turns: frozenset[str] = frozenset()

    def start(self, history: list[PublicHistoryEntry]) -> None:
        """Take the session's history as the run starts, before its turn.

        Its entries belong to earlier runs, so streaming output skips them and
        text output never answers with one.
        """
        self._earlier_turns = frozenset(
            entry.turn_id for entry in history if entry.turn_id is not None
        )
        self._emitted.update(entry.id for entry in history)

    def consume(self, event: AppServerEvent) -> None:
        if self._format is not OutputFormat.STREAMING:
            return
        match event:
            case HistoryEntryAdded(entry=entry) | HistoryEntryUpdated(entry=entry):
                self._emit_completed(entry)
            case _:
                pass

    def consume_teleport(self, event: TeleportEvent) -> None:
        if isinstance(event, TeleportComplete):
            self._teleport_url = event.url
        if self._format is OutputFormat.STREAMING:
            self._write_json(event)
            return
        if self._format is OutputFormat.JSON:
            return
        match event:
            case TeleportSummarizingContext():
                self._print("Summarizing context...")
            case TeleportCheckingGit():
                self._print("Preparing workspace...")
            case TeleportPushRequired(unpushed_count=count):
                self._print(f"Pushing {count} commit(s)...")
            case TeleportPushing():
                self._print("Syncing with remote...")
            case TeleportStartingWorkflow():
                self._print("Teleporting...")
            case TeleportComplete():
                pass
            case TeleportFailed():
                pass

    def finalize(self, history: list[PublicHistoryEntry]) -> str | None:
        if self._format is OutputFormat.STREAMING:
            return None
        if self._format is OutputFormat.JSON:
            history_json = [
                entry.model_dump(mode="json", by_alias=True) for entry in history
            ]
            payload: object = history_json
            if self._teleport_url is not None:
                payload = {"history": history_json, "teleportUrl": self._teleport_url}
            json.dump(payload, self._stream, indent=2, ensure_ascii=False)
            self._stream.write("\n")
            self._stream.flush()
            return None
        if self._teleport_url is not None:
            return self._teleport_url
        return _last_assistant_text([
            entry
            for entry in history
            if entry.turn_id is not None and entry.turn_id not in self._earlier_turns
        ])

    def _emit_completed(self, entry: PublicHistoryEntry) -> None:
        if (
            entry.id in self._emitted
            or entry.generation_status is not PublicEntryGenerationStatus.COMPLETED
        ):
            return
        self._emitted.add(entry.id)
        self._write_json(entry)

    def _write_json(self, value: BaseModel) -> None:
        json.dump(
            value.model_dump(mode="json", by_alias=True),
            self._stream,
            ensure_ascii=False,
        )
        self._stream.write("\n")
        self._stream.flush()

    def _print(self, text: str) -> None:
        print(text, file=self._stream)


def run_programmatic(
    *,
    harness_options: LocalHarnessOptions,
    prompt: str,
    output_format: OutputFormat = OutputFormat.TEXT,
    teleport: bool = False,
    stop: StopRequests,
) -> RunReport:
    logger.info("USER: %s", prompt)
    return asyncio.run(
        run_headless(
            harness_options=harness_options,
            prompt=prompt,
            output=ProgrammaticOutput(output_format),
            teleport=teleport,
            stop=stop,
        )
    )


async def run_headless(
    *,
    harness_options: LocalHarnessOptions,
    prompt: str,
    stop: StopRequests,
    output: ProgrammaticOutput | None = None,
    teleport: bool = False,
    harness_host: LocalHarnessHost | None = None,
) -> RunReport:
    """Run ``prompt`` as one headless turn on the running event loop.

    The run ends at the first of its own end and ``stop``. ``output`` defaults
    to text output, which writes nothing. A caller that must close the host
    process itself passes its own ``harness_host``; otherwise the run's harness
    owns one for the life of the process.
    """
    return await _ProgrammaticRun(
        harness_options=harness_options,
        prompt=prompt,
        output=output or ProgrammaticOutput(OutputFormat.TEXT),
        teleport=teleport,
        stop=stop,
        harness_host=harness_host,
    ).execute()


@dataclass
class _ProgrammaticRun:
    harness_options: LocalHarnessOptions
    prompt: str
    output: ProgrammaticOutput
    teleport: bool
    stop: StopRequests
    session: AppServerSession | None = None
    outcome: RunOutcome = RunOutcome.FINISHED
    error: RunError | None = None
    final_response: str | None = None
    config: dict[str, JsonValue] | None = None
    stopping: bool = False
    # A caller that must close the host process itself passes its own host;
    # otherwise the run's harness owns one for the life of the process.
    harness_host: LocalHarnessHost | None = None

    async def execute(self) -> RunReport:
        work = asyncio.create_task(self._drive())
        grace_ends: float | None = None
        try:
            stopped = await _first_stop(work, self.stop)
            # One grace period covers everything after this point, so a run
            # that stops exits about STOP_GRACE_SECONDS later at worst.
            grace_ends = _grace_ends()
            if stopped is None:
                await self._settle(work)
            else:
                await self._stop(work, grace_ends)
                self.outcome = stopped
                self.error = None
            if self.stop.requested is RunOutcome.ABORTED:
                # However the run then ended, the server's abort is why.
                self.outcome = RunOutcome.ABORTED
                self.error = self.stop.abort_error
            return await self._report(grace_ends)
        finally:
            if not work.done():
                work.cancel()
            if self.session is not None:
                await _within(
                    grace_ends or _grace_ends(), self.session.close(), "close"
                )

    async def _drive(self) -> None:
        self.session = session = (
            await LocalHarness(self.harness_options).start()
            if self.harness_host is None
            else await self.harness_host.start(self.harness_options)
        )
        await session.resources.runtime.wait_until_ready()
        # Read as the session is ready, before the run's turn spends anything.
        self.stop.count_usage_from(session.resources.runtime.stats)
        self.config = (await session.resources.config.read_effective()).config
        # A sandboxed workspace is not on this host, so it has no trust state.
        if self.harness_options.sandbox is None:
            await _warn_if_workspace_untrusted(session)
        self.output.start(session.history)
        if self.stopping:
            return
        try:
            if self.teleport:
                await _teleport(session, self.prompt, self.output)
            else:
                await _act(session, self.prompt, self.output, self.stop)
                self.outcome = _turn_outcome(session)
        except AppServerTurnError as exc:
            self.outcome = _turn_error_outcome(exc.error.code)
            self.error = RunError(message=exc.error.message, code=exc.error.code)
            return
        if not self.stopping:
            self.final_response = self.output.finalize(session.history)

    async def _settle(self, work: asyncio.Task[None]) -> None:
        """Wait for a run that ended on its own.

        Once the session exists, a failure (the app server going away
        mid-turn, say) ends the run as an infrastructure failure here, so the
        export still records the session and its journal. A usage error still
        reaches the caller, which reports it as one. A failure after the agent
        socket's server aborted the run is the abort's doing, and the run
        reports the abort instead.
        """
        try:
            await work
        except Exception as exc:
            if self.stop.requested is RunOutcome.ABORTED:
                logger.info("The run failed after it was aborted", exc_info=exc)
                return
            if self.session is None or is_usage_error(exc):
                raise
            logger.exception("Programmatic run failed")
            self.outcome = RunOutcome.INFRASTRUCTURE_FAILURE
            self.error = RunError(message=str(exc) or type(exc).__name__)

    async def _stop(self, work: asyncio.Task[None], grace_ends: float) -> None:
        self.stopping = True
        if self.session is not None:
            await _within(grace_ends, self.session.interrupt(), "interrupt")
            await asyncio.wait({work}, timeout=_remaining(grace_ends))
        if not work.done():
            work.cancel()
        elif not work.cancelled() and (error := work.exception()) is not None:
            logger.warning("The stopped run failed while settling", exc_info=error)
        if self.session is not None:
            self.final_response = self.output.finalize(self.session.history)

    async def _report(self, grace_ends: float) -> RunReport:
        session = self.session
        if session is None:
            return RunReport(result=RunResult(outcome=self.outcome, error=self.error))
        runtime = session.resources.runtime
        await _within(grace_ends, runtime.refresh(), "refresh")
        stats = runtime.stats
        usage = self.stop.run_usage(stats)
        return RunReport(
            result=RunResult(
                outcome=self._final_outcome(stats),
                stop_reason=turn_stop(_latest_turn(session)),
                error=self.error,
                session_id=session.session_id,
                usage=RunUsage(
                    input_tokens=usage.session_prompt_tokens,
                    output_tokens=usage.session_completion_tokens,
                    cached_input_tokens=usage.session_cached_tokens,
                    total_tokens=usage.session_total_llm_tokens,
                ),
                cost_usd=usage.session_cost,
                steps=stats.model_calls,
                config=self.config,
                warnings=[
                    RunWarning(message=issue.message, source=issue.file)
                    for issue in runtime.issues
                ],
            ),
            final_response=self.final_response,
            session_dir=Path(runtime.session_log.path)
            if runtime.session_log.path
            else None,
        )

    def _final_outcome(self, stats: AgentStatsSnapshot) -> RunOutcome:
        """A spent token or price budget wins over a turn that ended on its own.

        The run went past the limit it was given, whether or not the stop came
        in time to cut anything short. An error still wins, as it says more.
        """
        if self.error is not None:
            return self.outcome
        self.stop.observe_usage(stats)
        match self.stop.requested:
            case RunOutcome.TOKEN_LIMIT | RunOutcome.PRICE_LIMIT as spent:
                return spent
            case _:
                return self.outcome


def _grace_ends() -> float:
    return asyncio.get_running_loop().time() + STOP_GRACE_SECONDS


def _remaining(grace_ends: float) -> float:
    return max(0.0, grace_ends - asyncio.get_running_loop().time())


async def _within(grace_ends: float, step: Awaitable[object], name: str) -> None:
    """Run one step of winding a run down, giving up at the end of its grace.

    Winding down goes on after a failed step: the export matters more than the
    step, and the session may already be gone. Once its connection is gone for
    good, a step fails with whatever the closed connection raises (a plain
    ``RuntimeError`` among them), so every failure is logged and passed over.
    """
    try:
        async with asyncio.timeout_at(grace_ends):
            await step
    except Exception:
        logger.warning("Could not %s the session before exit", name, exc_info=True)


async def _first_stop(
    work: asyncio.Task[None], stop: StopRequests
) -> RunOutcome | None:
    stop_requested = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            {work, stop_requested}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        if not stop_requested.done():
            stop_requested.cancel()
            with suppress(asyncio.CancelledError):
                await stop_requested
    if work in done:
        return None
    return stop_requested.result()


async def _act(
    session: AppServerSession,
    prompt: str,
    output: ProgrammaticOutput,
    stop: StopRequests,
) -> None:
    async with aclosing(session.act(prompt)) as events:
        async for event in events:
            output.consume(event)
            match event:
                case CallbackRequested(callback=callback):
                    await session.deny_callback(callback)
                case StatsUpdated(params=params):
                    stop.observe_usage(params.stats)
                case _:
                    pass


def _turn_outcome(session: AppServerSession) -> RunOutcome:
    match turn_stop(_latest_turn(session)):
        case None:
            return RunOutcome.FINISHED
        case TurnStop.LIMIT:
            return RunOutcome.TURN_LIMIT
        case TurnStop.LENGTH:
            return RunOutcome.LENGTH
        case TurnStop.INTERRUPTED:
            # Nothing in this run asked for it, so something outside did.
            return RunOutcome.TERMINATED


def _latest_turn(session: AppServerSession) -> PublicTurn | None:
    return next(reversed(session.state.turns or []), None)


def _turn_error_outcome(code: str | None) -> RunOutcome:
    match code:
        case TurnErrorCode.INVALID_API_KEY | TurnErrorCode.INVALID_MODEL:
            return RunOutcome.CONFIG_ERROR
        case TurnErrorCode.CONTEXT_TOO_LONG | TurnErrorCode.RESPONSE_TOO_LONG:
            return RunOutcome.LENGTH
        case TurnErrorCode.REFUSAL:
            # The model's own answer, so the run is scored rather than dropped.
            return RunOutcome.REFUSAL
        case _:
            return RunOutcome.INFRASTRUCTURE_FAILURE


async def _warn_if_workspace_untrusted(session: AppServerSession) -> None:
    trust = await session.resources.workspace.trust_status()
    details = trust.details
    if trust.status != "untrusted" or details is None:
        return
    detected_files = list(
        dict.fromkeys([*details.detected_files, *details.repo_detected_files])
    )
    if not detected_files:
        return
    print(
        f"Warning: {details.cwd} is not trusted; project configuration "
        f"({', '.join(detected_files)}) will be ignored. Re-run with --trust "
        "to trust this folder temporarily.",
        file=sys.stderr,
    )


async def _teleport(
    session: AppServerSession, prompt: str, output: ProgrammaticOutput
) -> None:
    _, project_id = await session.resources.vibe_code.open_projects(
        for_teleport=True, prompt=prompt
    )
    if project_id is None:
        raise ProgrammaticTeleportError(
            "No Vibe Code project is linked to this repository"
        )
    async for event in session.resources.vibe_code.teleport(
        prompt or None, project_id=project_id
    ):
        output.consume_teleport(event)
        if isinstance(event, TeleportPushRequired):
            await session.resources.vibe_code.respond_to_push(
                event.operation_id, approved=True
            )
        if isinstance(event, TeleportFailed):
            raise ProgrammaticTeleportError(event.error.message)


def _last_assistant_text(history: list[PublicHistoryEntry]) -> str | None:
    return next(
        (
            entry.text
            for entry in reversed(history)
            if isinstance(entry, PublicMessageEntry)
            and entry.role == "assistant"
            and entry.text
        ),
        None,
    )
