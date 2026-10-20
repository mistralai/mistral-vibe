from __future__ import annotations

from pydantic import BaseModel, Field, PrivateAttr

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.utils import (
    DEFAULT_SENSITIVE_PATTERNS,
    ToolPath,
    resolve_file_tool_permission,
)


class EditArgs(BaseModel):
    file_path: ToolPath = Field(description="The absolute path to the file to modify")
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
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATTERNS),
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )


class Edit(BaseTool[EditArgs, EditResult, EditConfig, BaseToolState]):
    def resolve_permission(self, args: EditArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.file_path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
            workspace=self.workspace,
            scratchpad_dir=self.scratchpad_dir,
        )
