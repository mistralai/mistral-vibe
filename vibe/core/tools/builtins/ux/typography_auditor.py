"""UX Designer tool: audit typography (fonts, sizes, hierarchy) in CSS/HTML."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
import re
from typing import TYPE_CHECKING, ClassVar, final

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import resolve_file_tool_permission
from vibe.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class TypographyAuditorArgs(BaseModel):
    path: str = Field(
        description="Path to CSS file, SCSS file, or directory containing styles.",
    )


class TypographyAuditorResult(BaseModel):
    path: str
    font_families: list[str] = Field(default_factory=list)
    font_sizes: list[str] = Field(default_factory=list)
    line_heights: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    summary: str


class TypographyAuditorConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


def _audit_typography(content: str) -> tuple[list[str], list[str], list[str], list[str]]:
    font_families: list[str] = []
    font_sizes: list[str] = []
    line_heights: list[str] = []
    issues: list[str] = []

    for m in re.finditer(
        r"font-family\s*:\s*([^;}+]+)",
        content,
        re.IGNORECASE,
    ):
        fam = m.group(1).strip().strip("'\"").split(",")[0].strip()
        if fam and fam not in font_families:
            font_families.append(fam)

    for m in re.finditer(
        r"font-size\s*:\s*([^;}+]+)",
        content,
        re.IGNORECASE,
    ):
        sz = m.group(1).strip()
        if sz and sz not in font_sizes:
            font_sizes.append(sz)

    for m in re.finditer(
        r"line-height\s*:\s*([^;}+]+)",
        content,
        re.IGNORECASE,
    ):
        lh = m.group(1).strip()
        if lh and lh not in line_heights:
            line_heights.append(lh)

    if len(font_families) > 5:
        issues.append("Many font families - consider consolidating to a typography scale")
    if len(font_sizes) > 10:
        issues.append("Many font sizes - consider a type scale (e.g. 1.25 ratio)")
    if not font_sizes and not font_families:
        issues.append("No typography rules found in file")

    return (font_families, font_sizes, line_heights, issues)


class TypographyAuditor(
    BaseTool[
        TypographyAuditorArgs,
        TypographyAuditorResult,
        TypographyAuditorConfig,
        BaseToolState,
    ],
    ToolUIData[TypographyAuditorArgs, TypographyAuditorResult],
):
    description: ClassVar[str] = (
        "Audit typography in CSS/SCSS files: font families, sizes, line heights, "
        "and hierarchy consistency."
    )

    @final
    async def run(
        self, args: TypographyAuditorArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | TypographyAuditorResult, None]:
        base = Path(args.path).expanduser()
        if not base.is_absolute():
            base = Path.cwd() / base

        if not base.exists():
            raise ToolError(f"Path not found: {base}")

        paths: list[Path] = []
        if base.is_file():
            paths = [base]
        else:
            for ext in {".css", ".scss", ".sass", ".less"}:
                paths.extend(base.rglob(f"*{ext}"))

        all_families: list[str] = []
        all_sizes: list[str] = []
        all_heights: list[str] = []
        all_issues: list[str] = []

        for p in paths[:30]:
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            fam, sz, lh, iss = _audit_typography(content)
            all_families.extend(fam)
            all_sizes.extend(sz)
            all_heights.extend(lh)
            all_issues.extend(f"{p.name}: {i}" for i in iss)

        all_families = list(dict.fromkeys(all_families))
        all_sizes = list(dict.fromkeys(all_sizes))
        all_heights = list(dict.fromkeys(all_heights))
        all_issues = list(dict.fromkeys(all_issues))

        summary = (
            f"Audited {len(paths)} file(s). "
            f"{len(all_families)} font(s), {len(all_sizes)} size(s), "
            f"{len(all_issues)} issue(s)."
        )

        yield TypographyAuditorResult(
            path=str(base),
            font_families=all_families,
            font_sizes=all_sizes,
            line_heights=all_heights,
            issues=all_issues,
            summary=summary,
        )

    def resolve_permission(self, args: TypographyAuditorArgs) -> ToolPermission | None:
        return resolve_file_tool_permission(
            args.path,
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
        )

    @classmethod
    def format_call_display(cls, args: TypographyAuditorArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Auditing typography: {args.path}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, TypographyAuditorResult):
            return ToolResultDisplay(
                success=False,
                message=event.error or event.skip_reason or "No result",
            )
        return ToolResultDisplay(
            success=True,
            message=event.result.summary,
            warnings=event.result.issues[:3] if event.result.issues else [],
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Auditing typography"
