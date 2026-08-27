from __future__ import annotations

from textual.pilot import Pilot

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui.widgets.messages import ReasoningMessage


class SnapshotTestAppWithReasoningContent(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        fake_backend = FakeBackend(
            chunks=[
                mock_llm_chunk(
                    content="",
                    reasoning_content="Let me think about this step by step...",
                ),
                mock_llm_chunk(
                    content="",
                    reasoning_content=" First, I need to understand the question.",
                ),
                mock_llm_chunk(
                    content="", reasoning_content=" Then I can formulate a response."
                ),
                mock_llm_chunk(content="The answer to your question is 42."),
                mock_llm_chunk(content=" This is the ultimate answer."),
            ]
        )
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=self._current_agent_name,
            enable_streaming=True,
            backend=fake_backend,
        )
        super().__init__(agent_loop=agent_loop)


class SnapshotTestAppWithInterleavedReasoning(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        fake_backend = FakeBackend(
            chunks=[
                mock_llm_chunk(
                    content="", reasoning_content="Let me think about this..."
                ),
                mock_llm_chunk(content="Here's "),
                mock_llm_chunk(content="the "),
                mock_llm_chunk(content="first "),
                mock_llm_chunk(content="part "),
                mock_llm_chunk(content="of the answer. "),
                mock_llm_chunk(content="", reasoning_content="Now let me verify..."),
                mock_llm_chunk(content="And here's the conclusion!"),
            ]
        )
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=self._current_agent_name,
            enable_streaming=True,
            backend=fake_backend,
        )
        super().__init__(agent_loop=agent_loop)


class SnapshotTestAppWithHiddenReasoning(BaseSnapshotTestApp):
    """Same stream as the reasoning app, but thinking nodes are hidden."""

    def __init__(self) -> None:
        config = default_config(show_thinking_nodes=False)
        fake_backend = FakeBackend(
            chunks=[
                mock_llm_chunk(
                    content="",
                    reasoning_content="Let me think about this step by step...",
                ),
                mock_llm_chunk(
                    content="",
                    reasoning_content=" First, I need to understand the question.",
                ),
                mock_llm_chunk(
                    content="", reasoning_content=" Then I can formulate a response."
                ),
                mock_llm_chunk(content="The answer to your question is 42."),
                mock_llm_chunk(content=" This is the ultimate answer."),
            ]
        )
        super().__init__(config=config)
        self.agent_loop = build_test_agent_loop(
            config=config,
            agent_name=self._current_agent_name,
            enable_streaming=True,
            backend=fake_backend,
        )


def test_snapshot_shows_reasoning_content(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"What is the answer?")
        await pilot.press("enter")
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_reasoning_content.py:SnapshotTestAppWithReasoningContent",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_hides_reasoning_content(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"What is the answer?")
        await pilot.press("enter")
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_reasoning_content.py:SnapshotTestAppWithHiddenReasoning",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_shows_reasoning_content_expanded(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"What is the answer?")
        await pilot.press("enter")
        await pilot.pause(0.5)

        reasoning_msg = pilot.app.query_one(ReasoningMessage)
        await pilot.click(reasoning_msg)
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_reasoning_content.py:SnapshotTestAppWithReasoningContent",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_shows_interleaved_reasoning(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"Explain this to me")
        await pilot.press("enter")
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_reasoning_content.py:SnapshotTestAppWithInterleavedReasoning",
        terminal_size=(120, 36),
        run_before=run_before,
    )


class SnapshotTestAppWithBufferedReasoningTransition(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        fake_backend = FakeBackend(
            chunks=[
                mock_llm_chunk(
                    content="", reasoning_content="Analyzing the problem..."
                ),
                mock_llm_chunk(content="", reasoning_content=" Considering options..."),
                mock_llm_chunk(content="", reasoning_content=" Making decision."),
                mock_llm_chunk(content="Here is my carefully considered answer."),
                mock_llm_chunk(content=" I hope this helps!"),
            ]
        )
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=self._current_agent_name,
            enable_streaming=True,
            backend=fake_backend,
        )
        super().__init__(agent_loop=agent_loop)


def test_snapshot_buffered_reasoning_yields_before_content(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"Give me an answer")
        await pilot.press("enter")
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_reasoning_content.py:SnapshotTestAppWithBufferedReasoningTransition",
        terminal_size=(120, 36),
        run_before=run_before,
    )
