"""Wrapper script that intercepts LLM calls when mocking is enabled.

This script is used to mock the LLM calls when testing the CLI.
Mocked returns are stored in the VIBE_MOCK_LLM_DATA environment variable.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import os
import sys
from unittest.mock import patch

from pydantic import ValidationError

from tests import TESTS_ROOT
from tests.mock.utils import MOCK_DATA_ENV_VAR
from vibe.core.types import LLMChunk


def mock_llm_output() -> None:
    sys.path.insert(0, str(TESTS_ROOT))

    # Apply mocking before importing any vibe modules
    mock_data_str = os.environ.get(MOCK_DATA_ENV_VAR)
    if not mock_data_str:
        raise ValueError(f"{MOCK_DATA_ENV_VAR} is not set")
    mock_data = json.loads(mock_data_str)
    try:
        chunks = [LLMChunk.model_validate(chunk) for chunk in mock_data]
    except ValidationError as e:
        raise ValueError(f"Invalid mock data: {e}") from e

    chunk_iterable = iter(chunks)

    async def mock_complete(*args, **kwargs) -> LLMChunk:
        return next(chunk_iterable)

    async def mock_complete_streaming(*args, **kwargs) -> AsyncGenerator[LLMChunk]:
        yield next(chunk_iterable)

    patch(
        "vibe.core.llm.backend.mistral.MistralBackend.complete",
        side_effect=mock_complete,
    ).start()
    patch(
        "vibe.core.llm.backend.generic.GenericBackend.complete",
        side_effect=mock_complete,
    ).start()
    patch(
        "vibe.core.llm.backend.mistral.MistralBackend.complete_streaming",
        side_effect=mock_complete_streaming,
    ).start()
    patch(
        "vibe.core.llm.backend.generic.GenericBackend.complete_streaming",
        side_effect=mock_complete_streaming,
    ).start()


if __name__ == "__main__":
    mock_llm_output()

    from vibe.acp.entrypoint import main

    main()
