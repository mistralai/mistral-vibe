from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, final

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.scratchpad import is_scratchpad_path
from vibe.core.session.image_snapshot import snapshot_image
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
from vibe.core.types import (
    IMAGE_EXTENSIONS,
    FileImageSource,
    ImageAttachment,
    ToolStreamEvent,
)

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class ReadImageArgs(BaseModel):
    path: str = Field(description="The absolute path to the image file to read")


class ReadImageResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    alias: str
    mime_type: str
    image_path: str = Field(exclude=True)


class ReadImageConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )


class ReadImageState(BaseToolState):
    pass


class ReadImage(
    BaseTool[ReadImageArgs, ReadImageResult, ReadImageConfig, ReadImageState],
    ToolUIData[ReadImageArgs, ReadImageResult],
):
    def resolve_permission(self, args: ReadImageArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
        )

    def _resolve_path(self, raw_path: str) -> Path:
        if not raw_path.strip():
            raise ToolError("path cannot be empty")

        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()

        if not path.exists():
            raise ToolError(f"File not found at: {path}")
        if path.is_dir():
            raise ToolError(f"Path is a directory, not a file: {path}")
        return path

    def get_result_images(
        self, result: ReadImageResult
    ) -> list[ImageAttachment] | None:
        if not result.image_path:
            return None
        return [
            ImageAttachment(
                source=FileImageSource(path=Path(result.image_path)),
                alias=result.alias,
                mime_type=result.mime_type,
            )
        ]

    @final
    async def run(
        self, args: ReadImageArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ReadImageResult, None]:
        source_path = self._resolve_path(args.path)

        ext = source_path.suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            raise ToolError(
                f"Unsupported image extension '{ext}'. "
                f"Supported: {', '.join(sorted(IMAGE_EXTENSIONS))}"
            )

        session_dir = ctx.session_dir if ctx is not None else None
        att = snapshot_image(
            source_path, alias=source_path.name, session_dir=session_dir
        )

        if ctx is not None:
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                tool_call_id=ctx.tool_call_id,
                message="Reading image...",
            )

        yield ReadImageResult(
            path=str(source_path),
            alias=source_path.name,
            mime_type=att.mime_type,
            image_path=str(att.source.path)
            if isinstance(att.source, FileImageSource)
            else "",
        )

    @classmethod
    def format_call_display(cls, args: ReadImageArgs) -> ToolCallDisplay:
        suffix = "(scratchpad)" if is_scratchpad_path(args.path) else ""
        return ToolCallDisplay(summary=f"Reading {args.path}", suffix=suffix)

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ReadImageResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        suffix = "(scratchpad)" if is_scratchpad_path(event.result.path) else ""
        return ToolResultDisplay(
            success=True, message=f"Read image from {event.result.alias}", suffix=suffix
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Reading image"
