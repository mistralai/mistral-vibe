from __future__ import annotations

from textual.pilot import Pilot

from tests.conftest import build_test_agent_loop
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from vibe.core.types import LLMMessage, Role


class SnapshotTestAppWithInjectedMessages(BaseSnapshotTestApp):
    """Simulates resuming a session that contains injected middleware messages.

    The injected plan-mode reminder between the two user turns must not
    appear in the rendered history.
    """

    def __init__(self) -> None:
        agent_loop = build_test_agent_loop(config=default_config())
        agent_loop.messages.extend([
            LLMMessage(role=Role.user, content="Hello, can you help me?"),
            LLMMessage(role=Role.assistant, content="Sure! What do you need?"),
            # Middleware-injected plan mode reminder — should be hidden
            LLMMessage(
                role=Role.user,
                content="<vibe_warning>Plan mode is active. You MUST NOT make any edits.</vibe_warning>",
                injected=True,
            ),
            LLMMessage(role=Role.user, content="Please read my config file."),
            LLMMessage(
                role=Role.assistant, content="Here is the content of your config file."
            ),
        ])
        super().__init__(agent_loop=agent_loop)


def test_snapshot_session_resume_hides_injected_messages(
    snap_compare: SnapCompare,
) -> None:
    """Injected middleware messages must not be rendered when resuming a session."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_session_resume_injected.py:SnapshotTestAppWithInjectedMessages",
        terminal_size=(120, 36),
        run_before=run_before,
    )
