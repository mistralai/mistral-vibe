from __future__ import annotations

from collections.abc import Iterator
import time

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.log_level_picker import LogLevelPickerApp
from vibe.cli.textual_ui.widgets.messages import (
    BashOutputMessage,
    QueueHeaderMessage,
    SlashCommandMessage,
)
from vibe.cli.textual_ui.widgets.model_picker import ModelOption, ModelPickerApp
from vibe.cli.textual_ui.widgets.theme_picker import ThemePickerApp, sorted_theme_names
from vibe.cli.textual_ui.widgets.tools import ToolResultMessage
from vibe.observability.logging import (
    get_session_override,
    set_config_log_level,
    set_session_override,
)


@pytest.fixture(autouse=True)
def _reset_log_level_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    set_session_override(None)
    set_config_log_level(None)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("DEBUG_MODE", raising=False)
    yield
    set_session_override(None)
    set_config_log_level(None)


@pytest.fixture
def vibe_app() -> VibeApp:
    return build_test_vibe_app()


async def _wait_until(pilot, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.05)
    return False


@pytest.mark.asyncio
async def test_no_queue_header_when_empty(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        headers = list(vibe_app.query(QueueHeaderMessage))
        assert headers == []


@pytest.mark.asyncio
async def test_bash_submitted_during_running_bash_is_queued(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 1"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        chat_input.value = "!echo queued"
        await pilot.press("enter")

        assert len(vibe_app._input_queue) == 1
        assert vibe_app._input_queue.items[0].content == "echo queued"

        headers = list(vibe_app.query(QueueHeaderMessage))
        assert len(headers) == 1

        queued_bashes = [w for w in vibe_app.query(BashOutputMessage) if w._queued]
        assert len(queued_bashes) == 1

        await pilot.press("ctrl+c")
        assert await _wait_until(
            pilot, lambda: len(vibe_app._input_queue) == 0, timeout=2.0
        )
        await pilot.press("escape")
        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is None, timeout=5.0
        )


@pytest.mark.asyncio
async def test_slash_command_queued_when_busy(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 0.3"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=1.0)

        chat_input.post_message(ChatInputContainer.Submitted("/clear"))

        assert await _wait_until(
            pilot, lambda: len(vibe_app._input_queue) == 1, timeout=2.0
        )
        assert vibe_app._input_queue.items[0].kind.value == "command"
        assert vibe_app._input_queue.items[0].content == "/clear"
        assert vibe_app._input_queue.items[0].flushes_pending


def test_slash_command_message_strips_leading_slash_for_display() -> None:
    # The widget renders its own PROMPT_CHAR ("/"), so the stored raw input
    # "/clear" must not show as "//clear". Payload-path content has no slash.
    assert SlashCommandMessage("/clear").get_content() == "clear"
    assert SlashCommandMessage("model sonnet").get_content() == "model sonnet"


@pytest.mark.asyncio
async def test_loop_command_queued_when_busy(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 0.3"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=1.0)

        chat_input.post_message(ChatInputContainer.Submitted("/loop 30s ping"))

        assert await _wait_until(
            pilot, lambda: len(vibe_app._input_queue) == 1, timeout=2.0
        )
        assert vibe_app._input_queue.items[0].kind.value == "command"
        assert vibe_app._input_queue.items[0].content == "/loop 30s ping"


@pytest.mark.asyncio
async def test_theme_selected_while_idle_drains_immediately(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        picker = ThemePickerApp(
            theme_names=sorted_theme_names(), current_theme=vibe_app.config.theme
        )
        await vibe_app._switch_from_input(picker)
        assert await _wait_until(
            pilot, lambda: bool(list(vibe_app.query(ThemePickerApp))), timeout=2.0
        )

        target = "nord" if vibe_app.config.theme != "nord" else "ansi"
        vibe_app.post_message(ThemePickerApp.ThemeSelected(target))

        assert await _wait_until(
            pilot,
            lambda: vibe_app.config.theme == target and vibe_app._pending_theme is None,
            timeout=2.0,
        )
        assert len(vibe_app._input_queue) == 0
        assert not vibe_app._queue.draining


@pytest.mark.asyncio
async def test_ctrl_c_pops_last_queued_item_lifo(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=2.0)

        chat_input.value = "!echo first"
        await pilot.press("enter")
        chat_input.value = "!echo second"
        await pilot.press("enter")

        assert len(vibe_app._input_queue) == 2

        await pilot.press("ctrl+c")
        assert len(vibe_app._input_queue) == 1
        assert vibe_app._input_queue.items[0].content == "echo first"

        await pilot.press("escape")
        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_escape_pauses_queue_when_job_running(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=2.0)

        chat_input.value = "!echo queued"
        await pilot.press("enter")
        assert len(vibe_app._input_queue) == 1

        await pilot.press("escape")
        assert vibe_app._input_queue.paused
        assert len(vibe_app._input_queue) == 1

        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_drain_runs_queued_bashes_in_fifo_order(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 0.2"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=1.0)

        chat_input.value = "!echo first"
        await pilot.press("enter")
        chat_input.value = "!echo second"
        await pilot.press("enter")

        await _wait_until(
            pilot,
            lambda: (
                len([
                    message
                    for message in vibe_app.query(ToolResultMessage)
                    if message.tool_name == "shell"
                ])
                == 3
            ),
            timeout=5.0,
        )

        msgs = [
            message
            for message in vibe_app.query(ToolResultMessage)
            if message.tool_name == "shell"
        ]
        assert len(msgs) == 3
        assert vibe_app._input_queue.paused is False
        assert len(vibe_app._input_queue) == 0


@pytest.mark.asyncio
async def test_enter_on_empty_input_flushes_paused_queue(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=2.0)

        chat_input.value = "!echo queued"
        await pilot.press("enter")
        assert len(vibe_app._input_queue) == 1

        await pilot.press("escape")
        assert vibe_app._input_queue.paused

        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=10.0)

        chat_input.value = ""
        await pilot.press("enter")

        await _wait_until(
            pilot,
            lambda: (
                not vibe_app._input_queue.paused and len(vibe_app._input_queue) == 0
            ),
            timeout=10.0,
        )

        assert not vibe_app._input_queue.paused
        assert len(vibe_app._input_queue) == 0


@pytest.mark.asyncio
async def test_quit_warning_shows_queue_count(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        vibe_app._input_queue.append_prompt("a")
        vibe_app._input_queue.append_prompt("b")
        warning = vibe_app._queue.quit_warning_extra()
        assert warning == "2 queued messages will be discarded"

        vibe_app._input_queue.pop_last()
        warning = vibe_app._queue.quit_warning_extra()
        assert warning == "1 queued message will be discarded"

        vibe_app._input_queue.pop_last()
        assert vibe_app._queue.quit_warning_extra() == ""


@pytest.mark.asyncio
async def test_side_channel_theme_runs_while_busy(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        chat_input.post_message(ChatInputContainer.Submitted("/theme"))

        assert await _wait_until(
            pilot, lambda: bool(list(vibe_app.query(ThemePickerApp))), timeout=2.0
        )
        assert vibe_app._bash_task is not None
        assert not vibe_app._bash_task.done()
        assert not any("cannot be queued" in n.message for n in vibe_app._notifications)

        await pilot.press("escape")
        await pilot.press("escape")
        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_side_channel_exit_not_rejected_while_busy(
    vibe_app: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        exit_calls: list[dict] = []

        def fake_exit(self: VibeApp, **kwargs: object) -> None:
            exit_calls.append(kwargs)

        monkeypatch.setattr(VibeApp, "_exit_app", fake_exit)

        chat_input.post_message(ChatInputContainer.Submitted("/exit"))

        assert await _wait_until(pilot, lambda: len(exit_calls) > 0, timeout=2.0)
        assert not any("cannot be queued" in n.message for n in vibe_app._notifications)

        await pilot.press("escape")
        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_theme_selected_while_busy_enqueues_command(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        picker = ThemePickerApp(
            theme_names=sorted_theme_names(), current_theme=vibe_app.config.theme
        )
        await vibe_app._switch_from_input(picker)
        assert await _wait_until(
            pilot, lambda: bool(list(vibe_app.query(ThemePickerApp))), timeout=2.0
        )

        vibe_app.post_message(ThemePickerApp.ThemeSelected("nord"))

        assert await _wait_until(
            pilot, lambda: len(vibe_app._input_queue) == 1, timeout=2.0
        )
        item = vibe_app._input_queue.items[0]
        assert item.kind.value == "command"
        assert item.command_payload is not None
        assert vibe_app._pending_theme == "nord"

        await pilot.press("escape")
        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_discard_queued_theme_reverts_speculative_apply(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        original_theme = vibe_app.config.theme
        picker = ThemePickerApp(
            theme_names=sorted_theme_names(), current_theme=original_theme
        )
        await vibe_app._switch_from_input(picker)
        assert await _wait_until(
            pilot, lambda: bool(list(vibe_app.query(ThemePickerApp))), timeout=2.0
        )

        # Capture the resolved theme after the picker mounts so the discard
        # assertion compares against what _discard_theme reverts to.
        original_theme = vibe_app.theme

        vibe_app.post_message(ThemePickerApp.ThemeSelected("nord"))
        assert await _wait_until(
            pilot, lambda: vibe_app._pending_theme == "nord", timeout=2.0
        )
        assert vibe_app.theme == "nord"

        # Ctrl+C pops the queued persist before it drains; the speculative
        # apply must revert and the pending flag must clear.
        await pilot.press("ctrl+c")
        assert await _wait_until(
            pilot, lambda: len(vibe_app._input_queue) == 0, timeout=2.0
        )
        assert vibe_app._pending_theme is None
        assert vibe_app.theme == original_theme

        await pilot.press("escape")
        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_side_channel_model_runs_while_busy(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        chat_input.post_message(ChatInputContainer.Submitted("/model"))

        assert await _wait_until(
            pilot, lambda: bool(list(vibe_app.query(ModelPickerApp))), timeout=2.0
        )
        assert vibe_app._bash_task is not None
        assert not vibe_app._bash_task.done()
        assert not any("cannot be queued" in n.message for n in vibe_app._notifications)

        await pilot.press("escape")
        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_model_selected_while_busy_enqueues_command(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        picker = ModelPickerApp(
            models=[
                ModelOption(
                    alias="mistral-medium-3.5", display_name="Mistral Medium 3.5"
                ),
                ModelOption(alias="devstral-small", display_name="Devstral Small"),
            ],
            current_model=vibe_app._effective_model_alias,
            is_pinned=True,
            default_display_name="Mistral Medium 3.5",
        )
        await vibe_app._switch_from_input(picker)
        assert await _wait_until(
            pilot, lambda: bool(list(vibe_app.query(ModelPickerApp))), timeout=2.0
        )

        vibe_app.post_message(ModelPickerApp.ModelSelected("devstral-small"))

        assert await _wait_until(
            pilot, lambda: len(vibe_app._input_queue) == 1, timeout=2.0
        )
        item = vibe_app._input_queue.items[0]
        assert item.kind.value == "command"
        assert item.command_payload is not None
        assert vibe_app._pending_model == "devstral-small"
        assert vibe_app._effective_model_alias == "devstral-small"

        await pilot.press("escape")
        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_log_level_side_channel_defers_config_persist_while_busy(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 0.5"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        # /log-level is a side channel: the picker opens while the bash is busy.
        chat_input.post_message(ChatInputContainer.Submitted("/log-level"))

        assert await _wait_until(
            pilot, lambda: bool(list(vibe_app.query(LogLevelPickerApp))), timeout=2.0
        )
        assert vibe_app._bash_task is not None and not vibe_app._bash_task.done()

        picker = vibe_app.query_one(LogLevelPickerApp)
        picker.post_message(
            LogLevelPickerApp.Applied(
                session_level="DEBUG", config_level="ERROR", config_cleared=False
            )
        )
        await pilot.pause()

        # The session override is instant; the config.toml write is deferred to
        # the queue because config/write requires idle (CONFLICT while busy).
        assert get_session_override() == "DEBUG"
        assert len(vibe_app._input_queue) == 1
        assert vibe_app._input_queue.items[0].kind.value == "command"
        assert vibe_app.app_server.resources.config.current.log_level != "ERROR"

        # The job finishes naturally (no interrupt — that would pause the
        # queue), the queue drains, and the deferred persist lands.
        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is None, timeout=5.0
        )
        assert await _wait_until(
            pilot,
            lambda: vibe_app.app_server.resources.config.current.log_level == "ERROR",
            timeout=5.0,
        )


@pytest.mark.asyncio
async def test_queued_picker_command_blocks_drain_until_closed(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 0.3"
        await pilot.press("enter")

        assert await _wait_until(
            pilot, lambda: vibe_app._bash_task is not None, timeout=2.0
        )

        # Queue a command whose payload opens a picker, then a bash behind it.
        # A non-side-channel picker command (e.g. /mcp) reaches the drain as a
        # text-only COMMAND; this simulates that with a payload closure.
        async def open_picker() -> None:
            await vibe_app._switch_from_input(
                ThemePickerApp(
                    theme_names=sorted_theme_names(), current_theme=vibe_app.theme
                )
            )

        vibe_app._input_queue.append_command("picker", command_payload=open_picker)
        vibe_app._input_queue.append_bash("echo after")

        # Occupying bash finishes, the drain runs the picker payload, the picker
        # opens, and the drain blocks on it — so "echo after" must NOT have run.
        assert await _wait_until(
            pilot, lambda: bool(list(vibe_app.query(ThemePickerApp))), timeout=5.0
        )

        def shell_count() -> int:
            return len([
                m for m in vibe_app.query(ToolResultMessage) if m.tool_name == "shell"
            ])

        assert shell_count() == 1
        assert vibe_app._queue.draining

        # Close the picker → the drain unblocks → the queued bash runs.
        await pilot.press("escape")
        assert await _wait_until(
            pilot, lambda: not list(vibe_app.query(ThemePickerApp)), timeout=5.0
        )
        assert await _wait_until(pilot, lambda: shell_count() == 2, timeout=5.0)
        assert len(vibe_app._input_queue) == 0
