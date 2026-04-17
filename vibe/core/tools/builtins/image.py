from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
import os
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, final

from mistralai.client import Mistral
from mistralai.client.errors import SDKError
from pydantic import BaseModel, Field

_HTTP_UNAUTHORIZED = 401
_HTTP_RATE_LIMIT = 429

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolUIData
from vibe.core.types import ToolStreamEvent

if TYPE_CHECKING:
    pass


class ImageArgs(BaseModel):
    path: str | None = Field(default=None, description="Path to the input image file.")
    prompt: str | None = Field(
        default=None,
        description="Prompt to send alongside the image, or alone for text-to-image.",
    )
    output: str | None = Field(
        default=None, description="Save result to this file path."
    )
    model: str = Field(
        default="mistral-small-2506", description="Mistral vision model to use."
    )
    language: str | None = Field(
        default=None,
        description="Response language (e.g. 'french'). Defaults to prompt language.",
    )


class ImageResult(BaseModel):
    text: str
    saved_to: str | None = None


class ImageToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class Image(
    BaseTool[ImageArgs, ImageResult, ImageToolConfig, BaseToolState],
    ToolUIData[ImageArgs, ImageResult],
):
    description: ClassVar[str] = (
        "Analyze an image using Mistral vision models. "
        "Pass an image file with an optional prompt to describe or transform it. "
        "Text-to-image generation is not yet available on the Mistral API."
    )

    @final
    async def run(
        self, args: ImageArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ImageResult, None]:
        if args.path is None:
            if args.prompt:
                raise ToolError(
                    "Text-to-image is not yet available on the Mistral API."
                )
            raise ToolError(
                "Provide an image path. Use -p/--prompt to add a prompt alongside it."
            )

        image_path = Path(args.path)
        if not image_path.is_file():
            raise ToolError(f"File not found: {args.path}")

        try:
            text = await self._call_vision_api(
                image_path, args.prompt, args.model, args.language
            )
        except SDKError as e:
            status = getattr(e.raw_response, "status_code", None)
            if status == _HTTP_UNAUTHORIZED:
                raise ToolError("API key does not have access to vision models.") from e
            if status == _HTTP_RATE_LIMIT:
                raise ToolError("Rate limit reached. Try again later.") from e
            raise ToolError(f"Mistral API error: {e}") from e

        saved_to: str | None = None
        if args.output:
            out_path = Path(args.output)
            out_path.write_text(text, encoding="utf-8")
            saved_to = str(out_path)

        yield ImageResult(text=text, saved_to=saved_to)

    async def _call_vision_api(
        self, image_path: Path, prompt: str | None, model: str, language: str | None
    ) -> str:
        raw = image_path.read_bytes()
        b64 = base64.b64encode(raw).decode()
        suffix = image_path.suffix.lstrip(".").lower() or "jpeg"
        mime = f"image/{suffix}"

        user_text = prompt or "Describe this image."
        if language:
            user_text += f" Answer in {language}."

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ]

        api_key = os.getenv("MISTRAL_API_KEY")
        async with Mistral(api_key=api_key) as client:
            response = await client.chat.complete_async(model=model, messages=messages)

        content = response.choices[0].message.content
        if isinstance(content, list):
            return "".join(chunk.text for chunk in content if hasattr(chunk, "text"))
        return content or ""

    @classmethod
    def get_status_text(cls) -> str:
        return "Analyzing image"

    @classmethod
    def format_call_display(cls, args: ImageArgs) -> ToolCallDisplay:
        if args.path:
            summary = f"Analyzing {args.path}"
            if args.prompt:
                summary += f": {args.prompt[:60]}"
        else:
            summary = f"Generating image: {(args.prompt or '')[:60]}"
        return ToolCallDisplay(summary=summary)
