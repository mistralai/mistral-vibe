from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_utility_completion import FakeUtilityCompletion
from vibe.app_server._unified_vibe_code import UnifiedTeleportContextSummarizer
from vibe.core.types import LLMMessage, Role


@pytest.mark.asyncio
async def test_teleport_summary_uses_model_temperature_and_no_token_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config()
    completion = FakeUtilityCompletion("<summary>Fix the parser</summary>").install(
        monkeypatch
    )
    adapter = SimpleNamespace(config=config, launch_context=None, session_id="s")
    summarizer = UnifiedTeleportContextSummarizer(cast(Any, adapter))

    summary = await summarizer.summarize(
        [LLMMessage(role=Role.user, content="the parser is broken")], "fix it"
    )

    assert summary == "Fix the parser"
    [request] = completion.requests
    assert request.route.temperature == config.get_compaction_model().temperature
    assert request.config.max_tokens is None
