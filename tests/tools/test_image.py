from __future__ import annotations

import httpx
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.image import (
    Image,
    ImageArgs,
    ImageToolConfig,
    ImageResult,
)


@pytest.fixture
def image_tool():
    config = ImageToolConfig()
    return Image(config_getter=lambda: config, state=BaseToolState())


@pytest.fixture
def sample_image(tmp_path) -> Path:
    # Minimal 1x1 white JPEG
    jpeg_bytes = bytes([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
        0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
        0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
        0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
        0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
        0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
        0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
        0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
        0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00,
        0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0x09, 0x0A, 0x0B, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F,
        0x00, 0xF5, 0x00, 0xFF, 0xD9,
    ])
    p = tmp_path / "test.jpg"
    p.write_bytes(jpeg_bytes)
    return p


@pytest.mark.asyncio
async def test_describe_image_no_prompt(image_tool, sample_image):
    with patch.object(
        image_tool, "_call_vision_api", new=AsyncMock(return_value="A small test image.")
    ):
        result = await collect_result(image_tool.run(ImageArgs(path=str(sample_image))))

    assert isinstance(result, ImageResult)
    assert result.text == "A small test image."
    assert result.saved_to is None


@pytest.mark.asyncio
async def test_vision_with_prompt(image_tool, sample_image):
    with patch.object(
        image_tool, "_call_vision_api", new=AsyncMock(return_value="Snow added.")
    ):
        result = await collect_result(
            image_tool.run(ImageArgs(path=str(sample_image), prompt="add snow"))
        )

    assert result.text == "Snow added."


@pytest.mark.asyncio
async def test_text_to_image_not_available(image_tool):
    with pytest.raises(ToolError, match="not yet available"):
        await collect_result(image_tool.run(ImageArgs(prompt="a black cat")))


@pytest.mark.asyncio
async def test_file_not_found(image_tool):
    with pytest.raises(ToolError, match="File not found"):
        await collect_result(image_tool.run(ImageArgs(path="/nonexistent/photo.jpg")))


@pytest.mark.asyncio
async def test_output_saves_to_file(image_tool, sample_image, tmp_path):
    out = tmp_path / "result.txt"
    with patch.object(
        image_tool, "_call_vision_api", new=AsyncMock(return_value="A test image.")
    ):
        result = await collect_result(
            image_tool.run(ImageArgs(path=str(sample_image), output=str(out)))
        )

    assert out.exists()
    assert out.read_text() == "A test image."
    assert result.saved_to == str(out)


@pytest.mark.asyncio
async def test_no_args_raises(image_tool):
    with pytest.raises(ToolError):
        await collect_result(image_tool.run(ImageArgs()))


@pytest.mark.asyncio
async def test_api_unauthorized_clear_message(image_tool, sample_image):
    from mistralai.client.errors import SDKError

    err = SDKError("Unauthorized", raw_response=httpx.Response(401, content=b"Unauthorized"))
    with patch.object(
        image_tool,
        "_call_vision_api",
        new=AsyncMock(side_effect=err),
    ):
        with pytest.raises(ToolError, match="API key"):
            await collect_result(image_tool.run(ImageArgs(path=str(sample_image))))


@pytest.mark.asyncio
async def test_api_rate_limit_clear_message(image_tool, sample_image):
    from mistralai.client.errors import SDKError

    err = SDKError("Rate limited", raw_response=httpx.Response(429, content=b"Rate limited"))
    with patch.object(
        image_tool,
        "_call_vision_api",
        new=AsyncMock(side_effect=err),
    ):
        with pytest.raises(ToolError, match="Rate limit"):
            await collect_result(image_tool.run(ImageArgs(path=str(sample_image))))
