from __future__ import annotations

from typing import cast

from pydantic import ValidationError
import pytest

from vibe.app_server._tool_projection import (
    project_effect_detail,
    project_effect_output_value,
    project_effect_state,
)
from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    FailedEffectState,
    FileEditEffectOccurrence,
    FileEditEffectOutput,
    GenericEffectDetail,
    ShellEffectDetail,
    ShellEffectInput,
    ShellEffectOutput,
    SkillEffectDetail,
    SkillEffectInput,
    validate_history_entry,
)
from vibe.core.tools.builtins.bash import Bash, BashArgs, CapturedShellResult
from vibe.core.tools.builtins.edit import Edit, EditResult
from vibe.core.tools.builtins.experimental_bash import (
    ExperimentalBash,
    ExperimentalBashResult,
)
from vibe.core.tools.builtins.git_bash import ExperimentalGitBash, GitBash, GitBashArgs
from vibe.core.tools.builtins.grep import Grep, GrepResult
from vibe.core.tools.builtins.skill import Skill, SkillArgs
from vibe.core.tools.builtins.windows_shell import (
    ExperimentalWindowsShell,
    WindowsShell,
    WindowsShellArgs,
)
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.utils.tool_presentation import (
    EffectResultDisplay,
    ToolCallPresentation,
    ToolEffectKind,
    ToolResultPresentation,
)


def _display() -> EffectCallDisplay:
    return EffectCallDisplay(summary="summary", status_text="running")


def _presentation(kind: ToolEffectKind = ToolEffectKind.TOOL) -> ToolCallPresentation:
    return ToolCallPresentation(kind=kind, display=_display())


def test_semantic_effect_model_owns_its_projection() -> None:
    detail = project_effect_detail(
        "bash",
        BashArgs(command="uv run pytest", timeout=30),
        _presentation(ToolEffectKind.SHELL),
    )

    assert isinstance(detail, ShellEffectDetail)
    assert detail.tool_name == "bash"
    assert detail.input == ShellEffectInput(command="uv run pytest")


def test_git_bash_fallback_projects_as_shell_effect() -> None:
    event = ToolCallEvent(
        tool_name="git_bash",
        tool_class=GitBash,
        tool_call_id="call-1",
        args=GitBashArgs(command="pwd"),
    )

    detail = project_effect_detail(
        event.tool_name,
        event.args,
        ToolUIDataAdapter(GitBash).get_call_presentation(event),
    )

    assert isinstance(detail, ShellEffectDetail)
    assert detail.input == ShellEffectInput(command="pwd")


def test_powershell_fallback_projects_as_shell_effect() -> None:
    event = ToolCallEvent(
        tool_name="powershell",
        tool_class=WindowsShell,
        tool_call_id="call-1",
        args=WindowsShellArgs(command="Get-Location"),
    )

    detail = project_effect_detail(
        event.tool_name,
        event.args,
        ToolUIDataAdapter(WindowsShell).get_call_presentation(event),
    )

    assert isinstance(detail, ShellEffectDetail)
    assert detail.input == ShellEffectInput(command="Get-Location")


def test_arbitrary_tools_require_no_app_server_registration() -> None:
    for index in range(1000):
        detail = project_effect_detail(f"extension_{index}", None, _presentation())

        assert detail.kind is ToolEffectKind.TOOL
        assert detail.tool_name == f"extension_{index}"


def test_generic_tool_projection_preserves_json_input() -> None:
    detail = project_effect_detail(
        "weather", BashArgs(command="forecast Paris", timeout=30), _presentation()
    )

    assert detail.kind is ToolEffectKind.TOOL
    assert detail.input == {"command": "forecast Paris", "timeout": 30}


def test_stored_effect_args_missing_required_field_degrade_to_generic() -> None:
    # A subagent call stored before `agent` became required. Projecting it must
    # not raise so historical sessions stay readable and resumable.
    detail = project_effect_detail(
        "task",
        {"task": "Investigate the retry path"},
        _presentation(ToolEffectKind.SUBAGENT),
    )

    assert isinstance(detail, GenericEffectDetail)
    assert detail.tool_name == "task"
    assert detail.input == {"task": "Investigate the retry path"}


def test_generic_tool_call_uses_running_header_fallback() -> None:
    display = ToolUIDataAdapter(None).get_call_display(
        ToolCallEvent(
            tool_name="weather",
            tool_class=Bash,
            tool_call_id="call-1",
            args=BashArgs(command="forecast Paris", timeout=30),
        )
    )

    assert display.verb == "Running"
    assert display.message == "weather(command='forecast Paris', timeout=30)"


def test_result_projection_uses_the_same_tool_owned_contract() -> None:
    state = project_effect_state(
        ToolResultEvent(
            tool_name="bash",
            tool_class=Bash,
            result=CapturedShellResult(command="pwd", stdout="ok", stderr=""),
            tool_call_id="call-1",
            presentation=ToolResultPresentation(
                kind=ToolEffectKind.SHELL,
                display=EffectResultDisplay(success=True, message="Success"),
            ),
        )
    )

    completed = cast(CompletedEffectState, state)
    # A tool that never streams owes its whole output to append-only clients.
    assert completed.output == {
        "stdout": "ok",
        "stderr": "",
        "output": "",
        "truncated": False,
    }
    assert completed.output_text == "ok"


@pytest.mark.parametrize(
    ("output", "stdout", "stderr", "expected"),
    [
        ("out\rerr", "out", "err", "out\rerr"),
        ("", "src\n", "warn\n", "src\nwarn\n"),
        ("", "ok", "boom", "ok\nboom"),
        ("", "only stdout", "", "only stdout"),
        ("", "", "only stderr", "only stderr"),
    ],
)
def test_shell_output_transcript_reads_as_one_terminal(
    output: str, stdout: str, stderr: str, expected: str
) -> None:
    result = ShellEffectOutput(stdout=stdout, stderr=stderr, output=output)

    assert result.transcript == expected


def test_shell_projection_keeps_the_transcript_a_tool_streamed_itself() -> None:
    event = ToolResultEvent(
        tool_name="bash",
        tool_class=ExperimentalBash,
        result=ExperimentalBashResult(command="pwd", output="one two"),
        tool_call_id="call-1",
    )
    event = event.model_copy(
        update={
            "presentation": ToolUIDataAdapter(ExperimentalBash).get_result_presentation(
                event
            )
        }
    )

    completed = cast(
        CompletedEffectState, project_effect_state(event, output_text="one two")
    )

    assert completed.output_text == "one two"


def test_non_shell_projection_leaves_an_unstreamed_effect_empty() -> None:
    event = ToolResultEvent(
        tool_name="grep",
        tool_class=Grep,
        result=GrepResult(
            pattern="todo", matches="app.py:1", match_count=1, was_truncated=False
        ),
        tool_call_id="call-1",
        presentation=ToolResultPresentation(
            kind=ToolEffectKind.FILE_SEARCH,
            display=EffectResultDisplay(success=True, message="Success"),
        ),
    )

    completed = cast(CompletedEffectState, project_effect_state(event))

    assert completed.output_text == ""


def _project_shell_result(
    tool_class, result: ExperimentalBashResult | CapturedShellResult
) -> CompletedEffectState:
    event = ToolResultEvent(
        tool_name=tool_class.get_name(),
        tool_class=tool_class,
        result=result,
        tool_call_id="call-1",
    )
    presentation = ToolUIDataAdapter(tool_class).get_result_presentation(event)
    assert presentation.kind is ToolEffectKind.SHELL
    return cast(
        CompletedEffectState,
        project_effect_state(event.model_copy(update={"presentation": presentation})),
    )


@pytest.mark.parametrize(
    "tool_class", [ExperimentalBash, ExperimentalGitBash, ExperimentalWindowsShell]
)
def test_managed_shell_variants_project_output_for_terminal_safe_rendering(
    tool_class,
) -> None:
    result = ExperimentalBashResult(
        command="status", output="ready\rworking\x1b[2K\x07done"
    )

    completed = _project_shell_result(tool_class, result)

    assert completed.output == {
        "stdout": "ready\rworking\x1b[2K\x07done",
        "stderr": "",
        "output": "ready\rworking\x1b[2K\x07done",
        "truncated": False,
    }


@pytest.mark.parametrize("tool_class", [GitBash, WindowsShell])
def test_fallback_shell_variants_project_both_captured_streams(tool_class) -> None:
    result = CapturedShellResult(
        command="status", stdout="\x1b[32mok\x1b[0m", stderr="\x1b[31mwarning\x1b[0m"
    )

    completed = _project_shell_result(tool_class, result)

    assert completed.output == {
        "stdout": "\x1b[32mok\x1b[0m",
        "stderr": "\x1b[31mwarning\x1b[0m",
        "output": "",
        "truncated": False,
    }


def test_legacy_persisted_shell_payloads_still_project_on_resume() -> None:
    # Transcripts written before the shell result split persist the raw dump of
    # the old model, which carried `output` alongside `stdout` / `stderr`.
    legacy_output = {
        "command": "status",
        "session_id": "session-1",
        "status": "completed",
        "exit_code": 0,
        "shell": "/bin/bash",
        "background": False,
        "output": "ok",
        "next_cursor": 2,
        "truncated": False,
        "output_path": "",
        "stdout": "ok",
        "stderr": "warning",
        "returncode": 0,
    }

    projected = project_effect_output_value(ToolEffectKind.SHELL, legacy_output)

    assert projected == {
        "stdout": "ok",
        "stderr": "warning",
        "output": "ok",
        "truncated": False,
    }


def test_hook_replaced_result_projects_to_none_not_a_raw_dump() -> None:
    # A post_tool hook can replace a tool's native output with arbitrary text content
    # that does not fit the tool's output model (here bash's ShellEffectOutput has no
    # stdout/stderr). The projection must degrade to None -- a shape every typed client
    # (TUI, Le Chat, ACP) handles as "no structured output" -- rather than leak the raw
    # RustToolResult wire shape (`{"type": "success", "content": [...]}`), which no
    # client's output model can parse. The replacement text is carried in output_text.
    replaced = {
        "type": "success",
        "content": [{"type": "text", "text": "Output blocked by deny-plop."}],
    }

    assert project_effect_output_value(ToolEffectKind.SHELL, replaced) is None


def test_hook_replaced_result_degrades_to_none_for_every_kind() -> None:
    # For every kind, a post_tool deny (a raw RustToolResult wire shape) degrades to None
    # -- never the raw shape, and never a bogus empty model (e.g. todo -> {"todos": []}),
    # which would hide the replacement reason the TUI surfaces from output_text.
    replaced = {
        "type": "success",
        "content": [{"type": "text", "text": "Output blocked by a post_tool hook."}],
    }
    typed_kinds = [
        ToolEffectKind.SHELL,
        ToolEffectKind.FILE_READ,
        ToolEffectKind.FILE_WRITE,
        ToolEffectKind.FILE_EDIT,
        ToolEffectKind.FILE_SEARCH,
        ToolEffectKind.TODO,
        ToolEffectKind.WEB_SEARCH,
        ToolEffectKind.WEB_FETCH,
        ToolEffectKind.SKILL,
        ToolEffectKind.SUBAGENT,
    ]
    for kind in typed_kinds:
        assert project_effect_output_value(kind, replaced) is None, kind


def test_hook_replaced_result_passes_through_for_a_generic_tool() -> None:
    # A generic/MCP tool (ToolEffectKind.TOOL) has no typed output model, so a client
    # renders its output as opaque JSON. The projection leaves the replacement intact
    # (rather than degrading to None) -- the generic renderer accepts any JSON, so there
    # is nothing to crash and no reason to drop the payload.
    replaced = {
        "type": "success",
        "content": [{"type": "text", "text": "Blocked by a post_tool hook."}],
    }

    assert project_effect_output_value(ToolEffectKind.TOOL, replaced) == replaced


def test_file_edit_projection_preserves_all_rendering_occurrences() -> None:
    result = EditResult(
        file="example.py", message="updated", old_string="bar", new_string="qux"
    )
    result._ui_occurrences = [
        (1, "x = bar + 1", "x = qux + 1"),
        (2, "y = bar - 2", "y = qux - 2"),
    ]
    event = ToolResultEvent(
        tool_name="edit", tool_class=Edit, result=result, tool_call_id="call-1"
    )
    event = event.model_copy(
        update={"presentation": ToolUIDataAdapter(Edit).get_result_presentation(event)}
    )

    completed = cast(CompletedEffectState, project_effect_state(event))

    assert completed.output == FileEditEffectOutput(
        file="example.py",
        old_string="bar",
        new_string="qux",
        occurrences=[
            FileEditEffectOccurrence(
                start_line=1, old_text="x = bar + 1", new_text="x = qux + 1"
            ),
            FileEditEffectOccurrence(
                start_line=2, old_text="y = bar - 2", new_text="y = qux - 2"
            ),
        ],
    ).model_dump(mode="json", by_alias=True)


def test_file_search_projection_preserves_structured_match_locations() -> None:
    result = GrepResult(
        matches="src/a.py:10:TODO\nsrc/b.py:20:TODO", match_count=2, was_truncated=False
    )
    event = ToolResultEvent(
        tool_name="grep", tool_class=Grep, result=result, tool_call_id="call-1"
    )
    event = event.model_copy(
        update={"presentation": ToolUIDataAdapter(Grep).get_result_presentation(event)}
    )

    completed = cast(CompletedEffectState, project_effect_state(event))

    assert isinstance(completed.output, dict)
    assert completed.output["parsedMatches"] == [
        {"path": str(result.parsed_matches[0].path), "line": 10},
        {"path": str(result.parsed_matches[1].path), "line": 20},
    ]


def test_skill_uses_a_bounded_semantic_effect_kind() -> None:
    event = ToolCallEvent(
        tool_name="skill",
        tool_class=Skill,
        tool_call_id="call-1",
        args=SkillArgs(name="debug"),
    )
    detail = project_effect_detail(
        event.tool_name,
        event.args,
        ToolUIDataAdapter(Skill).get_call_presentation(event),
    )

    assert isinstance(detail, SkillEffectDetail)
    assert detail.input == SkillEffectInput(name="debug")


def test_effect_kind_is_strict_on_the_wire() -> None:
    invalid_entry = {
        "type": "effect",
        "id": "call-1",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "in_progress",
        "title": "bash",
        "detail": {
            "kind": "unknown_semantic_kind",
            "toolName": "bash",
            "input": {"command": "pwd"},
            "display": {"summary": "pwd", "statusText": "running"},
        },
        "state": {"status": "running", "outputText": ""},
    }

    with pytest.raises(ValidationError, match="kind"):
        validate_history_entry(invalid_entry)


def test_failed_effect_display_prefers_the_summary_the_tool_supplied() -> None:
    state = project_effect_state(
        ToolResultEvent(
            tool_name="bash",
            tool_class=None,
            error="<tool_error>bash failed: Return code: 3\n\nOutput:\nboom</tool_error>",
            error_display="Return code: 3",
            tool_call_id="call-1",
        ),
        output_text="boom",
    )

    assert isinstance(state, FailedEffectState)
    assert state.display.message == "Return code: 3"
    assert "boom" in state.error.message


def test_failed_effect_display_falls_back_to_the_error_text() -> None:
    state = project_effect_state(
        ToolResultEvent(
            tool_name="grep",
            tool_class=None,
            error="<tool_error>grep failed: no such file</tool_error>",
            tool_call_id="call-1",
        )
    )

    assert isinstance(state, FailedEffectState)
    assert state.display.message == "grep failed: no such file"
