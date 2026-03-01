"""UX Designer tool: analyze color palette for harmony and WCAG contrast."""

from __future__ import annotations

from collections.abc import AsyncGenerator
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
from vibe.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class ColorPaletteAnalyzerArgs(BaseModel):
    colors: str = Field(
        description="Comma- or space-separated hex colors (e.g. #FF5733, #33FF57).",
    )
    reference: str | None = Field(
        default=None,
        description="Optional reference hex for contrast checks (e.g. background).",
    )


class ColorPaletteAnalyzerResult(BaseModel):
    colors: list[str] = Field(default_factory=list)
    contrast_ratios: list[str] = Field(
        default_factory=list,
        description="WCAG contrast ratios if reference provided.",
    )
    wcag_aa_pass: bool = Field(
        description="Whether palette meets WCAG AA (4.5:1 for normal text).",
    )
    analysis: str = Field(description="Human-readable analysis.")


class ColorPaletteAnalyzerConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    min_contrast_ratio: float = Field(
        default=4.5,
        description="Minimum contrast ratio for AA compliance.",
    )


def _hex_to_rgb(hex_str: str) -> tuple[float, float, float] | None:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return None


def _relative_luminance(r: float, g: float, b: float) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast_ratio(l1: float, l2: float) -> float:
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


class ColorPaletteAnalyzer(
    BaseTool[
        ColorPaletteAnalyzerArgs,
        ColorPaletteAnalyzerResult,
        ColorPaletteAnalyzerConfig,
        BaseToolState,
    ],
    ToolUIData[ColorPaletteAnalyzerArgs, ColorPaletteAnalyzerResult],
):
    description: ClassVar[str] = (
        "Analyze a color palette for WCAG contrast and harmony. "
        "Input hex colors; optionally provide a reference for contrast checks."
    )

    @final
    async def run(
        self, args: ColorPaletteAnalyzerArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ColorPaletteAnalyzerResult, None]:
        raw = re.sub(r"[,;\s]+", " ", args.colors).strip()
        hex_list = re.findall(r"#[0-9a-fA-F]{3,8}\b", raw)
        if not hex_list:
            raise ToolError(
                "No valid hex colors found. Use format: #FF5733, #33FF57, etc."
            )

        colors = list(dict.fromkeys(hex_list))[:20]
        rgb_list = [_hex_to_rgb(c) for c in colors]
        valid = [c for c, r in zip(colors, rgb_list, strict=True) if r is not None]
        if not valid:
            raise ToolError("Could not parse any hex colors.")

        lums = [_relative_luminance(*r) for r in rgb_list if r is not None]
        contrast_ratios: list[str] = []
        wcag_aa_pass = True
        min_ratio = self.config.min_contrast_ratio

        ref_rgb = None
        if args.reference:
            ref_hex = re.search(r"#[0-9a-fA-F]{3,8}\b", args.reference)
            if ref_hex:
                ref_rgb = _hex_to_rgb(ref_hex.group())
        if ref_rgb:
            ref_lum = _relative_luminance(*ref_rgb)
            for c, lum in zip(valid, lums, strict=True):
                cr = _contrast_ratio(lum, ref_lum)
                contrast_ratios.append(f"{c} vs ref: {cr:.2f}:1")
                if cr < min_ratio:
                    wcag_aa_pass = False

        lines = [
            f"Analyzed {len(valid)} color(s): {', '.join(valid)}",
            "",
        ]
        if contrast_ratios:
            lines.append("Contrast ratios (vs reference):")
            for cr in contrast_ratios:
                lines.append(f"  {cr}")
            lines.append("")
            lines.append(
                f"WCAG AA ({min_ratio}:1): {'PASS' if wcag_aa_pass else 'FAIL'}"
            )
        else:
            lines.append(
                "Provide a reference color to check contrast (e.g. reference=#FFFFFF)"
            )

        yield ColorPaletteAnalyzerResult(
            colors=valid,
            contrast_ratios=contrast_ratios,
            wcag_aa_pass=wcag_aa_pass,
            analysis="\n".join(lines),
        )

    @classmethod
    def format_call_display(cls, args: ColorPaletteAnalyzerArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Analyzing color palette")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ColorPaletteAnalyzerResult):
            return ToolResultDisplay(
                success=False,
                message=event.error or event.skip_reason or "No result",
            )
        status = "WCAG AA ✓" if event.result.wcag_aa_pass else "WCAG AA ✗"
        return ToolResultDisplay(
            success=True,
            message=f"{len(event.result.colors)} colors - {status}",
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Analyzing colors"
