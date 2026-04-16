"""Regression test for streaming code-fenced ASCII art rendering.

Textual's Markdown.update() sets _last_parsed_line past fence openers,
causing append() to re-parse without fence context and collapse content
into paragraph text. This test streams a code-fenced diagram in small
chunks and verifies the rendered output preserves the fence block.
"""

from __future__ import annotations

from textual.pilot import Pilot

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend

# Chunks are sized so the first one contains the fence opener (```)
# plus content past the first newline — the exact pattern that triggers
# the bug.
_CHUNKS = [
    # First chunk: fence opener + content past newline (triggers the bug)
    "```\n  Client\n    |\n    |  POST",
    " /api/orders\n    v\n+---------+\n| Gateway ",
    "|\n+---------+\n    |\n    v\n+---------+\n| Service ",
    "|\n+---------+\n    |\n    v\n+----+----+\n|   DB    ",
    "|\n+---------+\n```",
]


class StreamingCodeFenceApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        fake_backend = FakeBackend(
            chunks=[mock_llm_chunk(content=chunk) for chunk in _CHUNKS]
        )
        super().__init__(config=config)
        self.agent_loop = build_test_agent_loop(
            config=config,
            agent_name=self._current_agent_name,
            enable_streaming=True,
            backend=fake_backend,
        )


def test_snapshot_streaming_code_fence_preserved(snap_compare: SnapCompare) -> None:
    """Verify that a streamed code-fenced diagram renders as a code block,
    not as collapsed paragraph text.
    """

    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"show diagram")
        await pilot.press("enter")
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_streaming_code_fence.py:StreamingCodeFenceApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
