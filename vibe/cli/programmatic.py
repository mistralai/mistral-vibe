from __future__ import annotations

import asyncio
from contextlib import aclosing
from enum import StrEnum, auto
import json
import sys
from typing import TextIO

from pydantic import BaseModel

from vibe.app_server.events import (
    AppServerEvent,
    CallbackRequested,
    HistoryEntryAdded,
    HistoryEntryUpdated,
)
from vibe.app_server.local import LocalHarness, LocalHarnessOptions
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicTurnStopReason,
    TeleportCheckingGit,
    TeleportComplete,
    TeleportEvent,
    TeleportFailed,
    TeleportPushing,
    TeleportPushRequired,
    TeleportStartingWorkflow,
    TeleportSummarizingContext,
)
from vibe.app_server.session import AppServerSession
from vibe.observability.logging import logger


class OutputFormat(StrEnum):
    TEXT = auto()
    JSON = auto()
    STREAMING = auto()


class ProgrammaticLimitError(RuntimeError):
    pass


class ProgrammaticTeleportError(RuntimeError):
    pass


class ProgrammaticOutput:
    def __init__(
        self, output_format: OutputFormat, stream: TextIO | None = None
    ) -> None:
        self._format = output_format
        self._stream = stream or sys.stdout
        self._emitted: set[str] = set()
        self._teleport_url: str | None = None

    def start(self, history: list[PublicHistoryEntry]) -> None:
        if self._format is not OutputFormat.STREAMING:
            return
        for entry in history:
            self._emit_completed(entry)

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
        return _last_assistant_text(history)

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
) -> str | None:
    logger.info("USER: %s", prompt)
    output = ProgrammaticOutput(output_format)

    async def run() -> str | None:
        session = await LocalHarness(harness_options).start()
        try:
            await session.resources.runtime.wait_until_ready()
            await _warn_if_workspace_untrusted(session)
            output.start(session.history)
            if teleport and session.resources.config.base.vibe_code_enabled:
                await _teleport(session, prompt, output)
            else:
                async with aclosing(session.act(prompt)) as events:
                    async for event in events:
                        output.consume(event)
                        if isinstance(event, CallbackRequested):
                            await session.deny_callback(event.callback)

                turn = session.state.latest_turn
                if turn is not None and turn.stop_reason is PublicTurnStopReason.LIMIT:
                    raise ProgrammaticLimitError(
                        _last_assistant_text(session.history)
                        or "The configured conversation limit was reached"
                    )
            return output.finalize(session.history)
        finally:
            await session.close()

    return asyncio.run(run())


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
