from __future__ import annotations

from collections.abc import AsyncGenerator
import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from vibe.core.logger import logger
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.builtins.bash import _background_dir
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolResultEvent, ToolStreamEvent

_MAX_CHUNK_BYTES = 16_000
"""Maximum number of bytes returned in a single ``bash_output`` call.

We tail from the end of the file so the most recent output is always
visible, and we clip to the same bound the foreground ``bash`` tool uses
for its output capture so agents see consistent sizing across tools.
"""


BackgroundStatus = Literal["running", "exited", "terminated", "unknown"]


class BashOutputToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    max_output_bytes: int = Field(
        default=_MAX_CHUNK_BYTES,
        description=(
            "Maximum bytes returned per call (tail of the stream). "
            "Older output is truncated when the process has produced more."
        ),
    )


class BashOutputArgs(BaseModel):
    bash_id: str = Field(
        description=(
            "Identifier returned by a previous ``bash`` call with "
            "``run_in_background=True``."
        )
    )


class BashOutputResult(BaseModel):
    bash_id: str
    command: str
    status: BackgroundStatus
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False


def _read_tail(path: Path, max_bytes: int) -> tuple[str, bool]:
    """Return the last ``max_bytes`` of ``path`` and whether it was truncated.

    The file is opened lazily so a missing path (e.g. the process has not
    produced anything on that stream yet) is treated as empty.  Binary
    content is decoded with ``replace`` so we never blow up on stray bytes
    from noisy build tools.
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return "", False
    except OSError:
        logger.exception("Failed to stat background output file %s", path)
        return "", False

    if size == 0:
        return "", False

    offset = 0
    truncated = False
    if size > max_bytes:
        offset = size - max_bytes
        truncated = True

    try:
        with path.open("rb") as fh:
            if offset:
                fh.seek(offset)
            data = fh.read(max_bytes)
    except OSError:
        logger.exception("Failed to read background output file %s", path)
        return "", False

    return data.decode("utf-8", errors="replace"), truncated


def _coerce_status(metadata: dict[str, object]) -> BackgroundStatus:
    raw = metadata.get("status")
    if raw in {"running", "exited", "terminated"}:
        return raw  # type: ignore[return-value]
    return "unknown"


def _coerce_returncode(metadata: dict[str, object]) -> int | None:
    value = metadata.get("returncode")
    return int(value) if isinstance(value, int) else None


def _coerce_command(metadata: dict[str, object]) -> str:
    value = metadata.get("command")
    return value if isinstance(value, str) else ""


def _load_metadata(metadata_path: Path) -> dict[str, object] | None:
    try:
        raw = metadata_path.read_text()
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("Failed to read background bash metadata %s", metadata_path)
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.exception(
            "Corrupt background bash metadata at %s (%r)", metadata_path, raw[:80]
        )
        return None
    return parsed if isinstance(parsed, dict) else None


class BashOutput(
    BaseTool[BashOutputArgs, BashOutputResult, BashOutputToolConfig, BaseToolState],
    ToolUIData[BashOutputArgs, BashOutputResult],
):
    description: ClassVar[str] = (
        "Read the current output and status of a background bash process."
    )

    @classmethod
    def format_call_display(cls, args: BashOutputArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"bash_output: {args.bash_id}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, BashOutputResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        return ToolResultDisplay(
            success=True, message=f"{event.result.bash_id} [{event.result.status}]"
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Reading background output"

    async def run(
        self, args: BashOutputArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashOutputResult, None]:
        if ctx is None or ctx.session_dir is None:
            raise ToolError("bash_output requires an active session directory.")

        bg_dir = _background_dir(ctx.session_dir)
        metadata = _load_metadata(bg_dir / f"{args.bash_id}.json")
        if metadata is None:
            raise ToolError(
                f"Unknown background bash id {args.bash_id!r}: no metadata file "
                f"under {bg_dir}."
            )

        max_bytes = self.config.max_output_bytes
        stdout, stdout_truncated = _read_tail(
            bg_dir / f"{args.bash_id}.stdout", max_bytes
        )
        stderr, stderr_truncated = _read_tail(
            bg_dir / f"{args.bash_id}.stderr", max_bytes
        )

        yield BashOutputResult(
            bash_id=args.bash_id,
            command=_coerce_command(metadata),
            status=_coerce_status(metadata),
            returncode=_coerce_returncode(metadata),
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
