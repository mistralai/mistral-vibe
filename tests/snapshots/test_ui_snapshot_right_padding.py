from __future__ import annotations

from textual.pilot import Pilot

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui.widgets.messages import ReasoningMessage

LONG_LINE = (
    "The history of computing stretches back thousands of years from the abacus"
    " through Charles Babbage's Analytical Engine to Alan Turing's theoretical"
    " foundations and the first electronic computers like ENIAC and UNIVAC which"
    " filled entire rooms and consumed enormous amounts of power while performing"
    " calculations that today's pocket calculators handle effortlessly and this"
    " remarkable progression continued through the invention of the transistor"
    " and integrated circuit leading to the personal computer revolution of the"
    " 1980s and the explosive growth of the internet in the 1990s transforming"
    " every aspect of modern life from communication to commerce to entertainment."
)


class AssistantLongLineApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        fake_backend = FakeBackend(
            mock_llm_chunk(
                content=LONG_LINE, prompt_tokens=10_000, completion_tokens=5_000
            )
        )
        super().__init__(backend=fake_backend)


def test_snapshot_right_padding_assistant_message(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"Tell me about computing")
        await pilot.press("enter")
        await pilot.pause(0.4)

    assert snap_compare(
        "test_ui_snapshot_right_padding.py:AssistantLongLineApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


class ReasoningLongLineApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        fake_backend = FakeBackend(
            chunks=[
                mock_llm_chunk(content="", reasoning_content=LONG_LINE),
                mock_llm_chunk(content="The answer is 42."),
            ]
        )
        super().__init__(config=config)
        self.agent_loop = build_test_agent_loop(
            config=config,
            agent_name=self._current_agent_name,
            enable_streaming=True,
            backend=fake_backend,
        )


def test_snapshot_right_padding_reasoning(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"Think hard")
        await pilot.press("enter")
        await pilot.pause(0.5)
        reasoning_msg = pilot.app.query_one(ReasoningMessage)
        await pilot.click(reasoning_msg)
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_right_padding.py:ReasoningLongLineApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
