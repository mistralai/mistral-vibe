"""UX Designer tool: analyze visual design quality of an image."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
import os
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, final

import mistralai
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


def _extract_score_from_analysis(content: str) -> float | None:
    for line in content.splitlines():
        if "score" in line.lower() and "/10" in line:
            parts = line.replace(",", ".").split()
            for i, p in enumerate(parts):
                if "/10" in p and i > 0:
                    try:
                        return float(parts[i - 1])
                    except (ValueError, IndexError):
                        pass
            break
    return None


_UX_ANALYSIS_PROMPT = """Analyze this UI/design screenshot for UX quality. Focus on:
- Visual hierarchy and information architecture
- Spacing, alignment, and visual rhythm
- Color usage and contrast
- Typography (readability, hierarchy)
- Usability issues and friction points
- WCAG accessibility considerations

Provide a structured analysis:
1. Overall score (0-10) with brief rationale
2. Strengths
3. Issues (prioritized by impact)
4. Recommendations
Keep the response concise but actionable."""


class AnalyzeDesignArgs(BaseModel):
    image_path: str = Field(
        description="Path to the image file (PNG, JPEG, WebP) to analyze."
    )
    focus: str | None = Field(
        default=None,
        description="Optional focus areas, e.g. 'hierarchy, colors, spacing'.",
    )


class AnalyzeDesignResult(BaseModel):
    analysis: str = Field(description="Structured UX analysis of the design.")
    score: float | None = Field(
        default=None,
        description="Overall quality score 0-10 if extractable.",
    )


class AnalyzeDesignConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    model: str = Field(
        default="mistral-small-latest",
        description="Mistral model with vision capabilities.",
    )
    max_image_bytes: int = Field(
        default=4 * 1024 * 1024,
        description="Maximum image file size in bytes (default 4MB).",
    )


class AnalyzeDesign(
    BaseTool[
        AnalyzeDesignArgs,
        AnalyzeDesignResult,
        AnalyzeDesignConfig,
        BaseToolState,
    ],
    ToolUIData[AnalyzeDesignArgs, AnalyzeDesignResult],
):
    description: ClassVar[str] = (
        "Analyze the visual design quality of a UI screenshot or design image. "
        "Evaluates hierarchy, spacing, colors, typography, and UX best practices."
    )

    @classmethod
    def is_available(cls) -> bool:
        return bool(os.getenv("MISTRAL_API_KEY"))

    @final
    async def run(
        self, args: AnalyzeDesignArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | AnalyzeDesignResult, None]:
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise ToolError("MISTRAL_API_KEY environment variable not set.")

        path = Path(args.image_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        if not path.exists():
            raise ToolError(f"Image file not found: {path}")

        if path.stat().st_size > self.config.max_image_bytes:
            raise ToolError(
                f"Image exceeds max size ({self.config.max_image_bytes} bytes). "
                "Use a smaller or compressed image."
            )

        suffix = path.suffix.lower()
        mime = "image/png"
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".webp":
            mime = "image/webp"

        raw = path.read_bytes()
        b64 = base64.standard_b64encode(raw).decode("ascii")
        image_url = f"data:{mime};base64,{b64}"

        prompt = _UX_ANALYSIS_PROMPT
        if args.focus:
            prompt += f"\n\nAdditional focus: {args.focus}"

        messages: list[dict[str, object]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": image_url},
                ],
            }
        ]

        client = mistralai.Mistral(api_key=api_key)

        try:
            response = await client.chat.complete_async(
                model=self.config.model,
                messages=messages,
            )
        except mistralai.SDKError as exc:
            raise ToolError(f"Mistral API error: {exc}") from exc

        content = response.choices[0].message.content or ""
        score = _extract_score_from_analysis(content)
        yield AnalyzeDesignResult(analysis=content, score=score)

    @classmethod
    def format_call_display(cls, args: AnalyzeDesignArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Analyzing design: {args.image_path}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, AnalyzeDesignResult):
            return ToolResultDisplay(
                success=False,
                message=event.error or event.skip_reason or "No result",
            )
        score_str = f" (score: {event.result.score})" if event.result.score is not None else ""
        return ToolResultDisplay(
            success=True,
            message=f"Design analysis complete{score_str}",
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Analyzing design"
