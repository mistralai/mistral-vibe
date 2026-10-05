from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.conftest import build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server import _unified_vibe_code as unified_vibe_code
from vibe.app_server._unified_vibe_code import UnifiedTeleportContextSummarizer
from vibe.core.types import LLMMessage, Role


@pytest.mark.asyncio
async def test_teleport_summary_uses_model_temperature_and_no_token_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config()
    backend = FakeBackend(mock_llm_chunk(content="<summary>Fix the parser</summary>"))
    monkeypatch.setattr(unified_vibe_code, "create_backend", lambda **_: backend)
    adapter = SimpleNamespace(config=config, launch_context=None, session_id="s")
    summarizer = UnifiedTeleportContextSummarizer(cast(Any, adapter))

    summary = await summarizer.summarize(
        [LLMMessage(role=Role.user, content="the parser is broken")], "fix it"
    )

    assert summary == "Fix the parser"
    assert backend.requests_temperatures == [config.get_compaction_model().temperature]
    assert backend.requests_max_tokens == [None]
