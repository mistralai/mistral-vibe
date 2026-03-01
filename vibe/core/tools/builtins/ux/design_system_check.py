"""UX Designer tool: validate files against design system conventions."""

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


class DesignSystemCheckArgs(BaseModel):
    path: str = Field(
        description="Path to file or directory to check.",
    )
    system: str = Field(
        default="generic",
        description="Design system reference (material, tailwind, generic).",
    )


class DesignSystemCheckResult(BaseModel):
    path: str
    system: str
    violations: list[str] = Field(default_factory=list)
    conformant: list[str] = Field(default_factory=list)
    score: float = Field(description="Conformance score 0-100.")
    summary: str


class DesignSystemCheckConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


def _check_generic(content: str) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    conformant: list[str] = []

    # Hardcoded colors
    hex_colors = re.findall(r"#[0-9a-fA-F]{3,8}\b", content)
    if len(hex_colors) > 5:
        violations.append("Many hardcoded hex colors - consider design tokens")
    elif hex_colors:
        conformant.append("Limited hardcoded colors")

    # Magic numbers for spacing
    spacing = re.findall(r"margin|padding|gap|spacing[:\s]+[\d.]+(?:px|rem|em)?", content)
    if len(spacing) > 10:
        violations.append("Magic spacing values - use a spacing scale")
    else:
        conformant.append("Spacing usage seems constrained")

    # Inline styles
    if 'style="' in content or "style={" in content:
        violations.append("Inline styles detected - prefer CSS classes/tokens")

    return (violations, conformant)


def _check_tailwind(content: str) -> tuple[list[str], list[str]]:
    v, c = _check_generic(content)
    if "className=" in content or "class=" in content:
        c.append("Uses utility classes")
    return (v, c)


def _check_material(content: str) -> tuple[list[str], list[str]]:
    v, c = _check_generic(content)
    if "Mui" in content or "material" in content.lower():
        c.append("Uses Material components")
    return (v, c)


_SYSTEM_CHECKERS = {
    "generic": _check_generic,
    "tailwind": _check_tailwind,
    "material": _check_material,
}


class DesignSystemCheck(
    BaseTool[
        DesignSystemCheckArgs,
        DesignSystemCheckResult,
        DesignSystemCheckConfig,
        BaseToolState,
    ],
    ToolUIData[DesignSystemCheckArgs, DesignSystemCheckResult],
):
    description: ClassVar[str] = (
        "Validate files against design system conventions (Material, Tailwind, or generic). "
        "Checks for hardcoded values, inline styles, and token usage."
    )

    @final
    async def run(
        self, args: DesignSystemCheckArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | DesignSystemCheckResult, None]:
        base = Path(args.path).expanduser()
        if not base.is_absolute():
            base = Path.cwd() / base

        if not base.exists():
            raise ToolError(f"Path not found: {base}")

        system = args.system.lower().strip()
        checker = _SYSTEM_CHECKERS.get(system, _check_generic)

        all_violations: list[str] = []
        all_conformant: list[str] = []

        paths: list[Path] = []
        if base.is_file():
            paths = [base]
        else:
            for ext in {".tsx", ".jsx", ".vue", ".css", ".scss", ".html"}:
                paths.extend(base.rglob(f"*{ext}"))

        for p in paths[:30]:
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            v, c = checker(content)
            all_violations.extend(f"{p.name}: {x}" for x in v)
            all_conformant.extend(c)

        all_violations = list(dict.fromkeys(all_violations))
        all_conformant = list(dict.fromkeys(all_conformant))
        total = len(all_violations) + len(all_conformant)
        score = 100.0 * len(all_conformant) / total if total else 100.0

        summary = (
            f"Checked {len(paths)} file(s) against '{args.system}'. "
            f"{len(all_violations)} violation(s), {len(all_conformant)} conformant check(s)."
        )

        yield DesignSystemCheckResult(
            path=str(base),
            system=args.system,
            violations=all_violations,
            conformant=all_conformant,
            score=round(score, 1),
            summary=summary,
        )

    def resolve_permission(self, args: DesignSystemCheckArgs) -> ToolPermission | None:
        return resolve_file_tool_permission(
            args.path,
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
        )

    @classmethod
    def format_call_display(cls, args: DesignSystemCheckArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Checking design system '{args.system}': {args.path}"
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, DesignSystemCheckResult):
            return ToolResultDisplay(
                success=False,
                message=event.error or event.skip_reason or "No result",
            )
        return ToolResultDisplay(
            success=True,
            message=f"Score: {event.result.score}/100 - {event.result.summary}",
            warnings=event.result.violations[:3] if event.result.violations else [],
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Checking design system"
