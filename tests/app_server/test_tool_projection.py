from __future__ import annotations

from typing import cast

from pydantic import ValidationError
import pytest

from vibe.app_server._tool_projection import project_effect_detail, project_effect_state
from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    FileEditEffectOccurrence,
    FileEditEffectOutput,
    ShellEffectDetail,
    ShellEffectInput,
    SkillEffectDetail,
    SkillEffectInput,
    validate_history_entry,
)
from vibe.core.tools.builtins.bash import Bash, BashArgs, BashResult
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
            result=BashResult(command="pwd", stdout="ok", stderr="", returncode=0),
            tool_call_id="call-1",
            presentation=ToolResultPresentation(
                kind=ToolEffectKind.SHELL,
                display=EffectResultDisplay(success=True, message="Success"),
            ),
        )
    )

    completed = cast(CompletedEffectState, state)
    assert completed.output == {"stdout": "ok", "stderr": ""}


@pytest.mark.parametrize(
    "tool_class", [ExperimentalBash, ExperimentalGitBash, ExperimentalWindowsShell]
)
def test_managed_shell_variants_project_output_for_terminal_safe_rendering(
    tool_class,
) -> None:
    result = ExperimentalBashResult(
        command="status",
        stdout="ready\rworking\x1b[2K\x07done",
        stderr="\x1b[31mwarning\x1b[0m",
    )
    event = ToolResultEvent(
        tool_name=tool_class.get_name(),
        tool_class=tool_class,
        result=result,
        tool_call_id="call-1",
    )
    presentation = ToolUIDataAdapter(tool_class).get_result_presentation(event)

    assert presentation.kind is ToolEffectKind.SHELL

    completed = cast(
        CompletedEffectState,
        project_effect_state(event.model_copy(update={"presentation": presentation})),
    )
    assert completed.output == {
        "stdout": "ready\rworking\x1b[2K\x07done",
        "stderr": "\x1b[31mwarning\x1b[0m",
    }


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
