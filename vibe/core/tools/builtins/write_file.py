from __future__ import annotations

from pydantic import BaseModel, Field

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.utils import (
    DEFAULT_SENSITIVE_PATTERNS,
    ToolPath,
    resolve_file_tool_permission,
)


class WriteFileArgs(BaseModel):
    file_path: ToolPath = Field(
        description="The absolute path to the file to write (must be absolute, not relative)"
    )
    content: str = Field(description="The content to write to the file")


class WriteFileResult(BaseModel):
    file_path: str
    bytes_written: int
    content: str


class WriteFileConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    sensitive_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATTERNS),
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )
    max_write_bytes: int = 64_000
    create_parent_dirs: bool = True


class WriteFile(
    BaseTool[WriteFileArgs, WriteFileResult, WriteFileConfig, BaseToolState]
):
    def resolve_permission(self, args: WriteFileArgs) -> PermissionContext | None:
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
