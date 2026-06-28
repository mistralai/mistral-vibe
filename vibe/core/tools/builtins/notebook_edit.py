from __future__ import annotations

from collections.abc import AsyncGenerator
from enum import StrEnum, auto
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import anyio
from pydantic import BaseModel, Field

from vibe.core.rewind.manager import FileSnapshot
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import resolve_file_tool_permission
from vibe.core.types import ToolStreamEvent
from vibe.core.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class CellType(StrEnum):
    CODE = auto()
    MARKDOWN = auto()


class EditMode(StrEnum):
    REPLACE = auto()
    INSERT = auto()
    DELETE = auto()


class NotebookEditArgs(BaseModel):
    notebook_path: str = Field(description="Path to the .ipynb notebook file.")
    cell_index: int = Field(
        ge=0, description="Zero-based index of the cell to edit, insert at, or delete."
    )
    new_source: str = Field(
        default="",
        description="New cell source. Required for replace/insert, ignored for delete.",
    )
    cell_type: CellType = Field(
        default=CellType.CODE,
        description="Cell type for replace/insert ('code' or 'markdown').",
    )
    edit_mode: EditMode = Field(
        default=EditMode.REPLACE,
        description="'replace' (default), 'insert', or 'delete'.",
    )


class NotebookEditResult(BaseModel):
    notebook_path: str
    edit_mode: EditMode
    cell_index: int
    cell_count: int


class NotebookEditConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )


class NotebookEdit(
    BaseTool[NotebookEditArgs, NotebookEditResult, NotebookEditConfig, BaseToolState],
    ToolUIData[NotebookEditArgs, NotebookEditResult],
):
    description: ClassVar[str] = (
        "Edit a Jupyter notebook (.ipynb) cell: replace its source, insert a new "
        "cell, or delete a cell, preserving the notebook JSON structure."
    )

    def resolve_permission(self, args: NotebookEditArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.notebook_path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
        )

    def get_file_snapshot(self, args: NotebookEditArgs) -> FileSnapshot | None:
        return self.get_file_snapshot_for_path(args.notebook_path)

    async def run(
        self, args: NotebookEditArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | NotebookEditResult, None]:
        path = self._resolve_path(args.notebook_path)
        notebook = self._load_notebook(path)
        cells = notebook["cells"]

        self._apply_edit(args, cells)

        await anyio.Path(path).write_text(
            json.dumps(notebook, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        yield NotebookEditResult(
            notebook_path=str(path),
            edit_mode=args.edit_mode,
            cell_index=args.cell_index,
            cell_count=len(cells),
        )

    @staticmethod
    def _resolve_path(raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.exists():
            raise ToolError(f"Notebook does not exist: {raw}")
        if path.suffix != ".ipynb":
            raise ToolError(f"Not a Jupyter notebook (.ipynb): {raw}")
        return path

    @staticmethod
    def _load_notebook(path: Path) -> dict[str, Any]:
        try:
            notebook = json.loads(read_safe(path).text)
        except json.JSONDecodeError as e:
            raise ToolError(f"Notebook is not valid JSON: {e}") from e
        if not isinstance(notebook, dict) or not isinstance(
            notebook.get("cells"), list
        ):
            raise ToolError("Notebook JSON is missing a 'cells' list.")
        return notebook

    def _apply_edit(self, args: NotebookEditArgs, cells: list[dict[str, Any]]) -> None:
        if args.edit_mode == EditMode.INSERT:
            if args.cell_index > len(cells):
                raise ToolError(
                    f"Cannot insert at {args.cell_index}: notebook has {len(cells)} cells."
                )
            cells.insert(args.cell_index, self._build_cell(args))
            return

        if not cells:
            raise ToolError("Notebook has no cells to edit.")
        if args.cell_index >= len(cells):
            raise ToolError(
                f"Cell index {args.cell_index} out of range ({len(cells)} cells)."
            )

        if args.edit_mode == EditMode.DELETE:
            del cells[args.cell_index]
            return

        cells[args.cell_index] = self._build_cell(args)

    @staticmethod
    def _build_cell(args: NotebookEditArgs) -> dict[str, Any]:
        source = args.new_source.splitlines(keepends=True)
        if args.cell_type == CellType.MARKDOWN:
            return {"cell_type": "markdown", "metadata": {}, "source": source}
        return {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": source,
        }

    @classmethod
    def format_call_display(cls, args: NotebookEditArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"{args.edit_mode.value} cell {args.cell_index} in {args.notebook_path}",
            content=args.new_source if args.edit_mode != EditMode.DELETE else "",
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, NotebookEditResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        return ToolResultDisplay(
            success=True,
            message=f"{event.result.edit_mode.value} cell {event.result.cell_index}",
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Editing notebook"
