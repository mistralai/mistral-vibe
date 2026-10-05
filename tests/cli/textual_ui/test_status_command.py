"""``/status``: statistics always render; the auth section is additive.

Legacy sessions keep the statistics-only output. Unified sessions append the
"Model & Provider" section, and a failed read leaves the statistics untouched
rather than growing an error or an empty section.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from textual.content import Content
from textual.widgets._markdown import MarkdownBlock

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    wait_until as _wait_until,
)
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.app_server.provider_auth import ProviderAuthView
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.provider_auth_status import render_provider_auth_section
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import UserCommandMessage


def _auth_view() -> ProviderAuthView:
    return ProviderAuthView(
        model_display_name="Claude Sonnet",
        provider_name="anthropic",
        api_base="https://api.anthropic.com/v1",
    )


def _mounted_text(mount: AsyncMock) -> str:
    message = mount.call_args[0][0]
    return message._content


@pytest.fixture
def vibe_app() -> VibeApp:
    return build_test_vibe_app()


class _BlockingBackend(FakeBackend):
    """Holds the first model turn open until ``release`` is set."""

    def __init__(self) -> None:
        super().__init__([[mock_llm_chunk(content="done")]] * 4)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        return await super().complete(**kwargs)


def _unified_with_auth_read(app: VibeApp) -> AsyncMock:
    """Put the app in a Unified session and stub the auth read; returns the
    mount spy so a test can assert on the rendered command message.
    """
    app.app_server.resources.runtime._state.experimental_harness = True
    app.app_server.resources.provider_auth.read = AsyncMock(return_value=_auth_view())
    mount = AsyncMock()
    app._mount_and_scroll = mount
    return mount


@pytest.mark.asyncio
async def test_legacy_session_keeps_statistics_only(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        mount = AsyncMock()
        vibe_app._mount_and_scroll = mount
        assert not vibe_app.app_server.resources.runtime.experimental_harness

        await vibe_app._show_status()

        text = _mounted_text(mount)
        assert "## Agent Statistics" in text
        assert "## Model & Provider" not in text


@pytest.mark.asyncio
async def test_unified_session_appends_model_and_provider(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        mount = _unified_with_auth_read(vibe_app)

        await vibe_app._show_status()

        text = _mounted_text(mount)
        assert "## Agent Statistics" in text
        assert "## Model & Provider" in text
        assert "- **Model**: Claude Sonnet" in text
        assert "- **Provider**: anthropic" in text


@pytest.mark.asyncio
async def test_failed_read_keeps_statistics_only(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        vibe_app.app_server.resources.runtime._state.experimental_harness = True
        vibe_app.app_server.resources.provider_auth.read = AsyncMock(
            side_effect=AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.METHOD_NOT_FOUND,
                    message="Method not found: providerAuth/read",
                )
            )
        )
        mount = AsyncMock()
        vibe_app._mount_and_scroll = mount

        await vibe_app._show_status()

        text = _mounted_text(mount)
        assert "## Agent Statistics" in text
        assert "## Model & Provider" not in text


@pytest.mark.asyncio
async def test_command_message_headings_have_no_line_jump(vibe_app: VibeApp) -> None:
    """Every section title in a command message must render the same way.

    The first Markdown block's margins are zeroed by the app stylesheet, so a
    later heading keeping Textual's default bottom margin rendered "## Agent
    Statistics" tight and "## Model & Provider" with a line jump below it.
    """
    async with vibe_app.run_test() as pilot:
        await vibe_app._mount_and_scroll(
            UserCommandMessage(
                "## Agent Statistics\n\n- **Steps**: 0\n\n## Model & Provider\n\n- **Model**: x"
            )
        )
        await pilot.pause()
        markdown = vibe_app.query_one(UserCommandMessage).query_one("Markdown")
        blocks = list(markdown.children)

    assert [type(block).__name__ for block in blocks] == [
        "MarkdownH2",
        "MarkdownBulletList",
        "MarkdownH2",
        "MarkdownBulletList",
    ]
    for heading, following in [(blocks[0], blocks[1]), (blocks[2], blocks[3])]:
        assert heading.region.y + heading.region.height == following.region.y


@pytest.mark.asyncio
async def test_dynamic_values_render_literally(vibe_app: VibeApp) -> None:
    """Markup-bearing dynamic values must reach the screen as plain text.

    String-level tests prove the markdown source is escaped; this proves the
    Textual ``Markdown`` widget renders the escaped source literally — a
    provider named ``[bold]`` appears as those characters, not as a style,
    and a ``~~`` pair stays tildes rather than striking the text through.
    """
    view = ProviderAuthView(
        model_display_name="Claude [bold] ~~struck~~ Sonnet",
        provider_name="anthropic",
        api_base="https://api.anthropic.com/v1",
    )
    async with vibe_app.run_test() as pilot:
        await vibe_app._mount_and_scroll(
            UserCommandMessage(render_provider_auth_section(view))
        )
        await pilot.pause()
        markdown = vibe_app.query_one(UserCommandMessage).query_one("Markdown")
        rendered = "\n".join(
            content.plain
            if isinstance(content := block.content, Content)
            else str(content)
            for block in markdown.query(MarkdownBlock)
        )

    assert "Claude [bold] ~~struck~~ Sonnet" in rendered


@pytest.mark.asyncio
async def test_status_during_model_turn_does_not_interrupt_it() -> None:
    """/status stays available mid-turn: it renders without queuing a prompt,
    interrupting the turn, or growing an error.
    """
    backend = _BlockingBackend()
    app = build_test_vibe_app(agent_loop=build_test_agent_loop(backend=backend))
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("block queue"))
        assert await _wait_until(pilot, backend.started.is_set)
        assert await _wait_until(
            pilot, lambda: app.app_server.turn_active and not app._queue
        )

        # Applied after the turn is live: runtime updates during turn startup
        # can overwrite the harness flag, and the read must reflect the state
        # at command time anyway.
        mount = _unified_with_auth_read(app)
        chat_input.post_message(ChatInputContainer.Submitted("/status"))

        assert await _wait_until(pilot, lambda: mount.call_count > 0)
        text = _mounted_text(mount)
        assert "## Agent Statistics" in text
        assert "## Model & Provider" in text
        assert not app._queue
        assert not any(
            "cannot be queued" in notification.message
            for notification in app._notifications
        )
        assert app.app_server.turn_active
        backend.release.set()


@pytest.mark.asyncio
async def test_status_during_shell_command_does_not_interrupt_it(
    vibe_app: VibeApp,
) -> None:
    """/status stays available while a shell command runs: it renders without
    interrupting the task or being rejected as queued work.
    """
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        mount = _unified_with_auth_read(vibe_app)
        chat_input.post_message(ChatInputContainer.Submitted("/status"))

        assert await _wait_until(pilot, lambda: mount.call_count > 0)
        text = _mounted_text(mount)
        assert "## Agent Statistics" in text
        assert "## Model & Provider" in text
        assert not any(
            "cannot be queued" in notification.message
            for notification in vibe_app._notifications
        )
        assert vibe_app._bash_task is not None

        await pilot.press("escape")
        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is None, timeout=5.0
        )
