from __future__ import annotations

from pydantic import BaseModel, Field

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.utils import (
    DEFAULT_SENSITIVE_PATTERNS,
    ToolPath,
    resolve_file_tool_permission,
)

DEFAULT_LINE_LIMIT = 2000
MAX_BYTES = 64_000


class ReadFileArgs(BaseModel):
    file_path: ToolPath = Field(description="The absolute path to the file to read")
    offset: int | None = Field(
        default=None,
        ge=1,
        description="The line number to start reading from (1-indexed)",
    )
    limit: int = Field(
        default=DEFAULT_LINE_LIMIT,
        gt=0,
        description="The maximum number of lines to read",
    )


class ReadFileResult(BaseModel):
    file_path: str
    content: str
    num_lines: int
    start_line: int
    requested_offset: int | None = None
    requested_limit: int = DEFAULT_LINE_LIMIT
    total_lines: int | None = None
    was_truncated: bool = False


class ReadFileConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    sensitive_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATTERNS),
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )
    max_read_bytes: int = Field(
        default=MAX_BYTES,
        gt=0,
        description="Maximum selected/output bytes to return in one call.",
    )


class ReadFileState(BaseToolState):
    pass


class ReadFile(BaseTool[ReadFileArgs, ReadFileResult, ReadFileConfig, ReadFileState]):
    def resolve_permission(self, args: ReadFileArgs) -> PermissionContext | None:
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
