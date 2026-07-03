from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import final

from pydantic import BaseModel, Field, PrivateAttr

from vibe.core.rewind.manager import FileSnapshot
from vibe.core.scratchpad import is_scratchpad_path
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
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.core.utils.io import (
    ReadSafeResult,
    atomic_replace,
    file_write_lock,
    read_safe_async,
)
from vibe.core.utils.text import line_contexts


class EditArgs(BaseModel):
    file_path: str = Field(description="The absolute path to the file to modify")
    old_string: str = Field(description="The text to replace")
    new_string: str = Field(
        description="The text to replace it with (must be different from old_string)"
    )
    replace_all: bool = Field(
        default=False,
        description="Replace all occurrences of old_string (default false)",
    )


class EditResult(BaseModel):
    file: str
    message: str
    old_string: str
    new_string: str
    # UI hint for the diff renderer; not part of the serialized result contract.
    # One entry per replaced occurrence (replace_all yields several), each as
    # (start_line, old_lines, new_lines) where old/new are the changed snippet
    # expanded to whole lines so the diff shows full lines per occurrence.
    _ui_occurrences: list[tuple[int, str, str]] = PrivateAttr(default_factory=list)

    @property
    def ui_start_lines(self) -> list[int]:
        return [start for start, _, _ in self._ui_occurrences]

    @property
    def ui_occurrences(self) -> list[tuple[int | None, str, str]]:
        if self._ui_occurrences:
            return list(self._ui_occurrences)
        return [(None, self.old_string, self.new_string)]


class EditConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )


class Edit(
    BaseTool[EditArgs, EditResult, EditConfig, BaseToolState],
    ToolUIData[EditArgs, EditResult],
):
    def resolve_permission(self, args: EditArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.file_path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
        )

    def get_file_snapshot(self, args: EditArgs) -> FileSnapshot | None:
        return self.get_file_snapshot_for_path(args.file_path)

    @classmethod
    def format_call_display(cls, args: EditArgs) -> ToolCallDisplay:
        suffix = "(scratchpad)" if is_scratchpad_path(args.file_path) else ""
        return ToolCallDisplay(
            summary=f"Editing {Path(args.file_path).name}",
            content=f"old_string: {args.old_string!r}\nnew_string: {args.new_string!r}",
            suffix=suffix,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, EditResult):
            suffix = "(scratchpad)" if is_scratchpad_path(event.result.file) else ""
            return ToolResultDisplay(
                success=True,
                message=f"Edited {Path(event.result.file).name}",
                suffix=suffix,
            )
        return ToolResultDisplay(
            success=False, message=event.error or event.skip_reason or "No result"
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Editing files"

    async def _read_file(self, file_path: Path) -> ReadSafeResult:
        return await read_safe_async(file_path, raise_on_error=True)

    async def _write_file(
        self, file_path: Path, content: str, encoding: str, newline: str
    ) -> None:
        await atomic_replace(file_path, content, encoding=encoding, newline=newline)

    @final
    async def run(
        self, args: EditArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | EditResult, None]:
        file_path = self._validate_args(args)

        try:
            async with file_write_lock(file_path):
                result = await self._read_file(file_path)
                original = result.text

                if args.old_string not in original:
                    raise ToolError(
                        f"String to replace not found in file.\n"
                        f"String: {args.old_string}"
                    )
                occurrences = original.count(args.old_string)
                if occurrences > 1 and not args.replace_all:
                    raise ToolError(
                        f"Found {occurrences} matches of the string to replace, "
                        f"but replace_all is false. To replace all occurrences, "
                        f"set replace_all to true. To replace only one occurrence, "
                        f"please provide more context to uniquely identify the "
                        f"instance.\nString: {args.old_string}"
                    )

                contexts = line_contexts(original, args.old_string)
                if not args.replace_all:
                    contexts = contexts[:1]

                modified = self._apply_edit(
                    original, args.old_string, args.new_string, args.replace_all
                )

                if modified != original:
                    await self._write_file(
                        file_path, modified, result.encoding, result.newline
                    )
        except UnicodeDecodeError as e:
            raise ToolError(
                f"Cannot edit {file_path}: file is not valid text "
                f"({e.encoding}, byte {e.start})"
            ) from e
        except PermissionError as e:
            raise ToolError(f"Permission denied accessing file: {file_path}") from e
        except OSError as e:
            raise ToolError(f"OS error accessing {file_path}: {e}") from e

        if args.replace_all:
            message = (
                "The file has been updated. All occurrences were successfully replaced"
            )
        else:
            message = "The file has been updated successfully."

        result = EditResult(
            file=str(file_path),
            message=message,
            old_string=args.old_string,
            new_string=args.new_string,
        )
        result._ui_occurrences = [
            (
                start,
                prefix + args.old_string + suffix,
                prefix + args.new_string + suffix,
            )
            for start, prefix, suffix in contexts
        ]
        yield result

    @final
    def _validate_args(self, args: EditArgs) -> Path:
        file_path_str = args.file_path.strip()
        if not file_path_str:
            raise ToolError("File path cannot be empty")

        if not args.old_string:
            raise ToolError(
                "old_string cannot be empty. Use write_file to create new files."
            )

        if args.old_string == args.new_string:
            raise ToolError(
                "No changes to make — old_string and new_string are identical"
            )

        file_path = Path(file_path_str).expanduser()
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        file_path = file_path.resolve()

        if not file_path.exists():
            raise ToolError(f"File does not exist: {file_path}")

        if not file_path.is_file():
            raise ToolError(f"Path is not a file: {file_path}")

        return file_path

    @staticmethod
    def _apply_edit(
        content: str, old_string: str, new_string: str, replace_all: bool
    ) -> str:
        if replace_all:
            return content.replace(old_string, new_string)
        return content.replace(old_string, new_string, 1)
