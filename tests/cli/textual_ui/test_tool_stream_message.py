from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.content import Content

from tests.stubs.app_server import CoreEventProjection
from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    EffectDetail,
    EffectResultDisplay,
    FileEditEffectDetail,
    FileWriteEffectDetail,
    GenericEffectDetail,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    RunningEffectState,
    UserQuestionEffectDetail,
)
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.core.tools.builtins.bash import Bash, BashArgs
from vibe.core.types import ToolCallEvent


class _ToolStreamApp(App[None]):
    CSS_PATH = Path(__file__).parents[3] / "vibe/cli/textual_ui/app.tcss"

    def compose(self) -> ComposeResult:
        yield Vertical(id="root")


def _effect(
    *,
    completed: bool,
    suffix: str = "",
    verb: str = "",
    message: str = "Found 9 matches",
    detail: EffectDetail | None = None,
) -> PublicEffectEntry:
    generation_status = (
        PublicEntryGenerationStatus.COMPLETED
        if completed
        else PublicEntryGenerationStatus.IN_PROGRESS
    )
    state = (
        CompletedEffectState(
            output_text=message,
            display=EffectResultDisplay(
                success=True, verb=verb, message=message, suffix=suffix
            ),
        )
        if completed
        else RunningEffectState(output_text=message)
    )
    if detail is None:
        detail = GenericEffectDetail(
            tool_name="grep",
            display=EffectCallDisplay(
                summary="Searching", status_text="Searching files"
            ),
        )
    return PublicEffectEntry(
        id="grep-1",
        session_id="session-1",
        turn_id="turn-1",
        created_at=1,
        updated_at=2,
        generation_status=generation_status,
        title="grep",
        detail=detail,
        state=state,
    )


@pytest.mark.asyncio
async def test_restored_terminal_effect_renders_result_immediately() -> None:
    call = ToolCallMessage(_effect(completed=True))

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield call

    async with _App().run_test() as pilot:
        await pilot.pause()

        assert call.get_content() == "Found 9 matches"
        assert call._text_widget is not None
        assert "Found 9 matches" in str(call._text_widget.render())
        assert not call._is_spinning


@pytest.mark.asyncio
async def test_terminal_effect_hides_transient_stream_message() -> None:
    app = _ToolStreamApp()

    async with app.run_test() as pilot:
        root = app.query_one("#root", Vertical)
        call = ToolCallMessage(_effect(completed=False))
        await root.mount(call)
        call.set_stream_message("grep: Found 9 matches")
        await pilot.pause()

        stream = call._stream_widget
        assert stream is not None
        assert stream.display

        await root.mount(ToolResultMessage(_effect(completed=True), call))
        await pilot.pause()

        assert not call._is_spinning
        assert not stream.display


@pytest.mark.asyncio
async def test_running_bash_uses_progressive_verb_and_message() -> None:
    projection = CoreEventProjection()
    projection.project(
        ToolCallEvent(
            tool_call_id="bash-1",
            tool_name="bash",
            tool_class=Bash,
            args=BashArgs(command="sleep 4"),
        )
    )
    entry = projection.history[-1]
    assert isinstance(entry, PublicEffectEntry)

    app = _ToolStreamApp()
    async with app.run_test() as pilot:
        call = ToolCallMessage(entry)
        await app.query_one("#root", Vertical).mount(call)
        await pilot.pause()

        assert call._verb_widget is not None
        assert call._text_widget is not None
        verb = call._verb_widget.render()
        message = call._text_widget.render()
        assert isinstance(verb, Content)
        assert isinstance(message, Content)
        assert verb.plain == "Running"
        assert message.plain == "sleep 4"
        assert call._header_row is not None
        assert call._header_row.has_class("running")
        assert call._header_row.has_class("collapsible-result")
        assert call._verb_widget.styles.text_opacity == 0.55
        assert call._text_widget.styles.text_opacity == 0.55

        call.stop_spinning()
        await pilot.pause()

        assert not call._header_row.has_class("running")


@pytest.mark.asyncio
async def test_collapsible_result_preserves_suffix_in_header() -> None:
    app = _ToolStreamApp()

    async with app.run_test() as pilot:
        root = app.query_one("#root", Vertical)
        entry = _effect(completed=True, suffix="(truncated)")
        call = ToolCallMessage(entry)
        await root.mount(call)
        result = ToolResultMessage(entry, call)
        await root.mount(result)
        await pilot.pause()

        suffix = result.query_one(".status-indicator-suffix", NoMarkupStatic)
        rendered = suffix.render()
        assert isinstance(rendered, Content)
        assert rendered.plain == "(truncated)"
        assert call.display is False


@pytest.mark.parametrize(
    "detail",
    [
        FileWriteEffectDetail(
            tool_name="write_file",
            display=EffectCallDisplay(
                summary="Writing dummy.txt",
                verb="Creating",
                message="dummy.txt",
                status_text="Writing file",
            ),
        ),
        FileEditEffectDetail(
            tool_name="edit",
            display=EffectCallDisplay(
                summary="Editing dummy.txt",
                verb="Editing",
                message="dummy.txt",
                status_text="Editing file",
            ),
        ),
        UserQuestionEffectDetail(
            tool_name="ask_user_question",
            display=EffectCallDisplay(
                summary="Asking a question",
                verb="Asking",
                message="Continue?",
                status_text="Waiting for user input",
            ),
        ),
    ],
    ids=["write_file", "edit", "ask_user_question"],
)
@pytest.mark.asyncio
async def test_running_always_expanded_result_stays_full_contrast(
    detail: EffectDetail,
) -> None:
    app = _ToolStreamApp()

    async with app.run_test() as pilot:
        call = ToolCallMessage(_effect(completed=False, detail=detail))
        await app.query_one("#root", Vertical).mount(call)
        await pilot.pause()

        assert call._header_row is not None
        assert call._verb_widget is not None
        assert call._text_widget is not None
        assert call._header_row.has_class("running")
        assert not call._header_row.has_class("collapsible-result")
        assert call._verb_widget.styles.text_opacity == 1.0
        assert call._text_widget.styles.text_opacity == 1.0
