"""How a headless run ended, and the export that records it.

The export is ``export.json`` in the caller's output directory; the session
journal is copied beside it under ``session/``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto
import json
import os
from pathlib import Path
import shutil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field

from vibe.app_server._config_introspect import redact_config
from vibe.app_server.models import TurnStop
from vibe.core.session.session_index import METADATA_FILENAME
from vibe.observability.logging import logger

EXPORT_FILENAME = "export.json"
JOURNAL_DIRNAME = "session"
# The session writer's half-written files: a write killed before its rename
# leaves one behind, holding the unredacted config for metadata.
_PARTIAL_WRITE_PATTERNS = ("*.json.tmp", "*.jsonl.tmp")


class RunOutcome(StrEnum):
    """The one taxonomy for how a headless run ended.

    New ways for a run to end are added here rather than defined by a caller.
    Each maps to one process exit code:

    - ``0`` the agent finished: ``finished``.
    - ``1`` the run could not start as asked: ``usage_error``, ``config_error``.
    - ``2`` infrastructure failed under the agent, e.g. the model API after its
      retries or the runtime: ``infrastructure_failure``.
    - ``3`` the agent stopped without finishing, on a limit or a model
      refusal: ``turn_limit``, ``token_limit``, ``price_limit``, ``deadline``,
      ``terminated``, ``length``, ``refusal``. What it left is still its own
      work, to be scored.
    - ``4`` the server on the other end of ``--agent-socket`` aborted the
      run: ``aborted``. Vibe does not know why; the server's message is the
      export's ``error.message``.
    """

    FINISHED = auto()
    USAGE_ERROR = auto()
    CONFIG_ERROR = auto()
    INFRASTRUCTURE_FAILURE = auto()
    TURN_LIMIT = auto()
    TOKEN_LIMIT = auto()
    PRICE_LIMIT = auto()
    DEADLINE = auto()
    TERMINATED = auto()
    LENGTH = auto()
    REFUSAL = auto()
    ABORTED = auto()

    @property
    def exit_code(self) -> int:
        match self:
            case RunOutcome.FINISHED:
                return 0
            case RunOutcome.USAGE_ERROR | RunOutcome.CONFIG_ERROR:
                return 1
            case RunOutcome.INFRASTRUCTURE_FAILURE:
                return 2
            case (
                RunOutcome.TURN_LIMIT
                | RunOutcome.TOKEN_LIMIT
                | RunOutcome.PRICE_LIMIT
                | RunOutcome.DEADLINE
                | RunOutcome.TERMINATED
                | RunOutcome.LENGTH
                | RunOutcome.REFUSAL
            ):
                return 3
            case RunOutcome.ABORTED:
                return 4


class HeadlessUsageError(Exception):
    """A headless run was asked for something it cannot do.

    It ends the run as ``usage_error``: the mistake is the caller's, so the same
    request fails the same way every time, unlike an infrastructure failure.
    """


class RunError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    code: str | None = None


class RunWarning(BaseModel):
    """Something the run went without, or worked around, that the caller may
    want to know: a skill or a config file the session could not load, say.

    Vibe gives a warning no meaning beyond its message, and the run went on
    regardless.
    """

    model_config = ConfigDict(extra="forbid")

    message: str
    # What the warning is about, such as a file or a skill, when it is about one.
    source: str | None = None


class RunUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    total_tokens: int


class RunLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_turns: int | None = None
    max_price: float | None = None
    max_tokens: int | None = None
    time_limit_s: float | None = None


class RunResult(BaseModel):
    """How a headless run ended, as far as the run itself can tell.

    ``usage``, ``cost_usd`` and ``steps`` count this run only: a run that
    resumes a session does not count the session's earlier runs.
    """

    model_config = ConfigDict(extra="forbid")

    outcome: RunOutcome
    stop_reason: TurnStop | None = None
    error: RunError | None = None
    session_id: str | None = None
    usage: RunUsage | None = None
    cost_usd: float | None = None
    # Model calls the session's own agent made, one per step of its loop:
    # neither its subagents' calls nor utility calls such as compaction. None
    # when it made none, or when the runtime does not count them.
    steps: int | None = None
    # The redacted config the session ran with, as the server resolved it.
    config: dict[str, JsonValue] | None = None
    # Left out of the export when there are none.
    warnings: list[RunWarning] | None = Field(
        default=None, exclude_if=lambda value: not value
    )


class RunExport(RunResult):
    # Ignored rather than forbidden: readers parse exports back, and
    # ``exit_code`` is computed, so a forbidding model rejects its own dump.
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    vibe_version: str
    # Relative to the output directory. The whole session's journal, earlier
    # runs included when the run resumed one.
    journal_dir: str | None = None
    started_at: datetime
    ended_at: datetime
    limits: RunLimits

    @computed_field
    @property
    def exit_code(self) -> int:
        return self.outcome.exit_code


def write_run_export(output_dir: Path, export: RunExport) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / EXPORT_FILENAME
    staged = output_dir / f".{EXPORT_FILENAME}.tmp"
    staged.write_text(export.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(staged, path)
    return path


def copy_journal(session_dir: Path, output_dir: Path) -> str:
    """Copy a session directory into the output directory; returns its name there.

    The copy's config snapshot is redacted as the export's is, since the
    output directory is shared more widely than the session directory.
    The copy is staged in a temp directory first. The previous journal is
    then set aside, the copy renamed into its place, and only then the
    previous journal removed, so a failed copy does not destroy the previous
    journal. A failed copy removes its staging directory, which may hold an
    unredacted snapshot.
    """
    target = output_dir / JOURNAL_DIRNAME
    staged = output_dir / f".{JOURNAL_DIRNAME}.tmp"
    previous = output_dir / f".{JOURNAL_DIRNAME}.old"
    _recover_previous_journal(previous, target)
    if staged.exists():
        shutil.rmtree(staged)
    try:
        shutil.copytree(
            session_dir,
            staged,
            symlinks=True,
            ignore=shutil.ignore_patterns(*_PARTIAL_WRITE_PATTERNS),
        )
        _redact_metadata_config(staged / METADATA_FILENAME)
        _swap_in(staged, target, previous)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return JOURNAL_DIRNAME


def _recover_previous_journal(previous: Path, target: Path) -> None:
    """Settle a journal an earlier, crashed copy left aside.

    With a journal in place, the one set aside is older and goes. Without one,
    the copy crashed between setting it aside and renaming its own copy in,
    so the one set aside is the latest journal and goes back.
    """
    if not previous.exists():
        return
    if target.exists():
        shutil.rmtree(previous)
    else:
        os.replace(previous, target)


def _swap_in(staged: Path, target: Path, previous: Path) -> None:
    if not target.exists():
        os.replace(staged, target)
        return
    # Rename rather than replace over the old journal: a directory cannot
    # replace a non-empty one, and a rename can be undone.
    os.replace(target, previous)
    try:
        os.replace(staged, target)
    except BaseException:
        os.replace(previous, target)
        raise
    shutil.rmtree(previous, ignore_errors=True)


def _redact_metadata_config(path: Path) -> None:
    if not path.is_file():
        return
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        # A snapshot that cannot be read cannot be redacted either.
        logger.warning(
            "Dropping the copied %s: it cannot be parsed, so it cannot be redacted",
            path.name,
        )
        path.unlink()
        return
    match metadata:
        case {"config": dict() as config}:
            metadata["config"] = redact_config(config)
        case _:
            return
    # Replace rather than write through, in case the copy kept a symlink.
    path.unlink()
    path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
