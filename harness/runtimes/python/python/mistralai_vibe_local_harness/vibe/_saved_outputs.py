"""Where the model reads back a tool output too large to hand it whole.

The Harness Core decides to save such an output and asks the host to write it.
The host writes it with the Session's records, which an export copies, then
asks the workspace for a copy the model's tools can open and gives the model
that copy's path. On the host, the record is that copy. With a Sandbox
Adapter, the copy is saved in the sandbox through the adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import secrets
from typing import Protocol

from mistralai_vibe_local_harness.vibe._file_tools import (
    SANDBOX_FILE_TOOL_TIMEOUT_SECONDS,
)
from mistralai_vibe_local_harness.vibe._sandbox import (
    SandboxAdapter,
    SandboxToolError,
    run_helper,
)


def build_saved_outputs(
    sandbox: SandboxAdapter | None, *, records: Callable[[], Path], session_id: str
) -> SavedOutputs:
    """The copies for a Session whose tools run in ``sandbox``, or on the host
    when it is None.

    ``records`` gives the directory the Session records saved outputs in, as it
    is when an output is saved.
    """
    if sandbox is None:
        return HostSavedOutputs(records)
    return SandboxSavedOutputs(sandbox, session_id=session_id or secrets.token_hex(8))


class SavedOutputs(Protocol):
    """The workspace's copies of the saved outputs the model reads back."""

    @property
    def read_roots(self) -> tuple[Path, ...]:
        """Workspace directories ``read_file`` may read the copies from."""
        ...

    async def publish(self, record: Path, content: str) -> str:
        """Make the output recorded at ``record`` readable by the model's tools,
        and return the path the model is given.
        """
        ...


@dataclass(frozen=True, slots=True)
class HostSavedOutputs:
    """The model's tools run on the host, so they read the records themselves."""

    records: Callable[[], Path]

    @property
    def read_roots(self) -> tuple[Path, ...]:
        return (self.records(),)

    async def publish(self, record: Path, content: str) -> str:
        return str(record)


class SandboxSavedOutputs:
    """Copies saved in an owner-only directory under the sandbox's temporary
    directory, which is only known from inside the sandbox.
    """

    def __init__(self, sandbox: SandboxAdapter, *, session_id: str) -> None:
        self._sandbox = sandbox
        self._session_id = session_id
        self._root: Path | None = None

    @property
    def read_roots(self) -> tuple[Path, ...]:
        return () if self._root is None else (self._root,)

    async def publish(self, record: Path, content: str) -> str:
        saved = await run_helper(
            self._sandbox,
            "save-tool-result",
            {"session_id": self._session_id, "name": record.name, "content": content},
            timeout=SANDBOX_FILE_TOOL_TIMEOUT_SECONDS,
        )
        path = saved.get("path") if isinstance(saved, dict) else None
        if not isinstance(path, str):
            raise SandboxToolError(f"Unexpected response to saving a result: {saved}")
        self._root = Path(path).parent
        return path


__all__ = ["SavedOutputs", "build_saved_outputs"]
