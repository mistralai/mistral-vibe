"""UX Designer tool: recommend component extraction and consolidation."""

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


class ComponentRecommenderArgs(BaseModel):
    path: str = Field(
        default="src/components",
        description="Path to component directory to analyze.",
    )


class ComponentRecommenderResult(BaseModel):
    path: str
    components: list[str] = Field(
        default_factory=list,
        description="List of discovered component files.",
    )
    duplicates: list[str] = Field(
        default_factory=list,
        description="Potential duplicate or similar components.",
    )
    recommendations: str = Field(
        description="Recommendations for consolidation.",
    )


class ComponentRecommenderConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


_COMPONENT_EXTENSIONS = {".tsx", ".jsx", ".vue", ".svelte", ".py"}


def _find_components(base: Path) -> list[Path]:
    found: list[Path] = []
    for ext in _COMPONENT_EXTENSIONS:
        for p in base.rglob(f"*{ext}"):
            if p.name.startswith("_") or "node_modules" in p.parts:
                continue
            found.append(p)
    return sorted(found)[:100]


def _similar_names(files: list[Path]) -> list[str]:
    names: dict[str, list[str]] = {}
    for p in files:
        stem = p.stem.lower()
        base = re.sub(r"(button|card|modal|input|form)$", "", stem)
        if base:
            key = base.rstrip("s")  # Button vs Buttons
            names.setdefault(key, []).append(p.name)
    return [
        f"Similar: {', '.join(v)}"
        for v in names.values()
        if len(v) > 1
    ][:10]


class ComponentRecommender(
    BaseTool[
        ComponentRecommenderArgs,
        ComponentRecommenderResult,
        ComponentRecommenderConfig,
        BaseToolState,
    ],
    ToolUIData[ComponentRecommenderArgs, ComponentRecommenderResult],
):
    description: ClassVar[str] = (
        "Analyze a component directory to find duplicates and suggest consolidation. "
        "Works with React, Vue, Svelte, and Python components."
    )

    @final
    async def run(
        self, args: ComponentRecommenderArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ComponentRecommenderResult, None]:
        base = Path(args.path).expanduser()
        if not base.is_absolute():
            base = Path.cwd() / base

        if not base.exists():
            raise ToolError(f"Path not found: {base}")

        if not base.is_dir():
            raise ToolError("Path must be a directory")

        components = _find_components(base)
        comp_names = [str(p.relative_to(base)) for p in components]
        duplicates = _similar_names(components)

        rec_lines = [
            f"Found {len(components)} component file(s).",
            "",
        ]
        if duplicates:
            rec_lines.append("Potential duplicates or similar components:")
            for d in duplicates:
                rec_lines.append(f"  - {d}")
            rec_lines.append("")
            rec_lines.append(
                "Consider consolidating into shared primitives or a design system."
            )
        else:
            rec_lines.append("No obvious duplicates detected. Review structure for consistency.")

        yield ComponentRecommenderResult(
            path=str(base),
            components=comp_names,
            duplicates=duplicates,
            recommendations="\n".join(rec_lines),
        )

    def resolve_permission(self, args: ComponentRecommenderArgs) -> ToolPermission | None:
        return resolve_file_tool_permission(
            args.path,
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
        )

    @classmethod
    def format_call_display(cls, args: ComponentRecommenderArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Analyzing components: {args.path}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ComponentRecommenderResult):
            return ToolResultDisplay(
                success=False,
                message=event.error or event.skip_reason or "No result",
            )
        return ToolResultDisplay(
            success=True,
            message=f"{len(event.result.components)} components, "
            f"{len(event.result.duplicates)} potential duplicate(s)",
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Analyzing components"
