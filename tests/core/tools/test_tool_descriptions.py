"""Tool descriptions are sourced from the sibling prompts/<tool>.md file and
exposed via get_full_description(); every argument carries a Field description.
"""

from __future__ import annotations

from vibe.core.tools.base import BaseTool
from vibe.core.tools.builtins.ask_user_question import AskUserQuestion
from vibe.core.tools.builtins.bash import Bash
from vibe.core.tools.builtins.edit import Edit
from vibe.core.tools.builtins.exit_plan_mode import ExitPlanMode
from vibe.core.tools.builtins.experimental_bash import (
    BashLogFile,
    BashOutput,
    BashSessions,
    BashStdin,
    ExperimentalBash,
)
from vibe.core.tools.builtins.grep import Grep
from vibe.core.tools.builtins.read_file import ReadFile
from vibe.core.tools.builtins.skill import Skill
from vibe.core.tools.builtins.task import Task
from vibe.core.tools.builtins.todo import Todo
from vibe.core.tools.builtins.web_fetch import WebFetch
from vibe.core.tools.builtins.web_search import WebSearch
from vibe.core.tools.builtins.write_file import WriteFile

ALL_BUILTINS: list[type[BaseTool]] = [
    AskUserQuestion,
    Bash,
    Edit,
    ExitPlanMode,
    Grep,
    ReadFile,
    Skill,
    Task,
    Todo,
    WebFetch,
    WebSearch,
    WriteFile,
]


def test_description_is_sourced_from_md() -> None:
    assert ReadFile.get_full_description() == ReadFile.get_tool_prompt()
    assert ReadFile.get_full_description().startswith("Use `read_file`")


def test_every_builtin_description_uses_the_shared_pattern() -> None:
    for cls in ALL_BUILTINS:
        description = cls.get_full_description()
        assert description.strip(), f"{cls.__name__} has an empty description"
        assert description.startswith("Use `"), (
            f"{cls.__name__} description should start with 'Use `<tool>` to …'"
        )


def test_experimental_bash_prompt_is_not_reused_by_companion_tools() -> None:
    prompt = ExperimentalBash.get_tool_prompt()

    assert prompt is not None
    assert "Stateful sessions" in prompt
    assert ExperimentalBash.get_full_description() == prompt

    companion_tools: tuple[type[BaseTool], ...] = (
        BashOutput,
        BashStdin,
        BashSessions,
        BashLogFile,
    )
    for cls in companion_tools:
        assert cls.get_tool_prompt() is None
        assert cls.get_full_description() == cls.description
        assert "Stateful sessions" not in cls.get_full_description()


def test_file_tools_use_unified_file_path_argument() -> None:
    for cls in (ReadFile, WriteFile, Edit):
        assert "file_path" in cls.get_parameters()["properties"]


def test_tool_names_are_unified() -> None:
    assert ReadFile.get_name() == "read_file"
    assert WriteFile.get_name() == "write_file"
    assert WebFetch.get_name() == "web_fetch"
    assert WebSearch.get_name() == "web_search"
