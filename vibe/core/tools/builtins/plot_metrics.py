from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

from pydantic import BaseModel, Field

from vibe.cli.textual_ui.widgets.braille_renderer import render_braille
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


class PlotMetricsArgs(BaseModel):
    source: str = Field(
        description=(
            "JSONL file to plot: one flat JSON object per line "
            '(e.g. {"epoch": 3, "loss": 0.42, "accuracy": 0.88}). '
            "Numeric fields are auto-detected."
        )
    )
    x: str | None = Field(
        default=None, description="x-axis key (default: first numeric key)"
    )
    y: list[str] | None = Field(
        default=None,
        description="series to plot (default: every other numeric key, max 4)",
    )
    title: str | None = Field(default=None, description="Chart title")
    width: int = Field(default=60, description="Chart width in terminal columns")
    height: int = Field(default=10, description="Height of each chart in rows")


class PlotMetricsResult(BaseModel):
    chart: str = Field(description="Rendered chart, already shown to the user")
    series: list[str]
    points: int


class PlotMetricsConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS  # read-only rendering


class PlotMetricsState(BaseToolState):
    pass


def _numeric_keys(rows: list[dict]) -> list[str]:
    keys: list[str] = []
    for row in rows:
        for key, value in row.items():
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and key not in keys
            ):
                keys.append(key)
    return keys


def _series_chart(
    points: list[tuple[float, float]], key: str, width: int, height: int
) -> str:
    dot_w, dot_h = width * 2, height * 4
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span_x, span_y = (x1 - x0) or 1.0, (y1 - y0) or 1.0

    def to_dot(x: float, y: float) -> complex:
        dx = (x - x0) / span_x * (dot_w - 1)
        dy = (dot_h - 1) - (y - y0) / span_y * (dot_h - 1)
        return complex(dx, dy)

    # dedupe per dot cell: render_braille sums dot indices, duplicates would
    # overflow past the braille block
    seen: set[tuple[int, int]] = set()
    coords = []
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        steps = max(2, int(abs(to_dot(bx, by).real - to_dot(ax, ay).real)) + 1)
        for i in range(steps + 1):
            t = i / steps
            dot = to_dot(ax + (bx - ax) * t, ay + (by - ay) * t)
            cell = (int(dot.real), int(dot.imag))
            if cell not in seen:
                seen.add(cell)
                coords.append(complex(*cell))

    body = render_braille(coords, dot_w, dot_h).split("\n")
    gutter = max(len(f"{v:.4g}") for v in (y0, y1))
    lines = []
    for i, row in enumerate(body):
        if i == 0:
            label = f"{y1:>{gutter}.4g} ┤"
        elif i == len(body) - 1:
            label = f"{y0:>{gutter}.4g} ┤"
        else:
            label = " " * gutter + " │"
        lines.append(label + row)
    footer = f"{' ' * gutter} └ {key}: x ∈ [{x0:.4g}, {x1:.4g}]"
    return "\n".join([*lines, footer])


class PlotMetrics(
    BaseTool[PlotMetricsArgs, PlotMetricsResult, PlotMetricsConfig, PlotMetricsState],
    ToolUIData[PlotMetricsArgs, PlotMetricsResult],
):
    @classmethod
    def format_call_display(cls, args: PlotMetricsArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Plotting {args.source}")

    @classmethod
    def format_result_display(cls, result: PlotMetricsResult) -> ToolResultDisplay:
        return ToolResultDisplay(
            success=True,
            message=result.chart,
            suffix=f"({result.points} points)",
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Rendering chart"

    async def run(
        self, args: PlotMetricsArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | PlotMetricsResult, None]:
        source = Path(args.source).expanduser()
        if not source.is_absolute():
            source = Path.cwd() / source
        if not source.is_file():
            raise ToolError(f"source file not found: {source}")

        rows = []
        for line in source.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        if not rows:
            raise ToolError(f"no JSONL rows found in {source}")

        keys = _numeric_keys(rows)
        if not keys:
            raise ToolError("no numeric fields found to plot")
        x_key = args.x or keys[0]
        y_keys = args.y or [k for k in keys if k != x_key][:4]

        charts = []
        if args.title:
            charts.append(args.title)
        total = 0
        for key in y_keys:
            points = [
                (float(r[x_key]), float(r[key]))
                for r in rows
                if isinstance(r.get(x_key), (int, float))
                and isinstance(r.get(key), (int, float))
            ]
            if len(points) < 2:
                continue
            total += len(points)
            charts.append(_series_chart(points, key, args.width, args.height))
        if not charts:
            raise ToolError("not enough numeric data to plot (need >= 2 points)")

        yield PlotMetricsResult(
            chart="\n\n".join(charts), series=y_keys, points=total
        )
