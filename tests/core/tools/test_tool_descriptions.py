"""Tool descriptions are sourced from the sibling prompts/<tool>.md file and
exposed via get_full_description(); every argument carries a Field description.
"""

from __future__ import annotations

from vibe.core.tools.base import BaseTool
from vibe.core.tools.builtins.edit import Edit
from vibe.core.tools.builtins.experimental_bash import (
    BashLogFile,
    BashOutput,
    BashSessions,
    BashStdin,
    ExperimentalBash,
)
from vibe.core.tools.builtins.git_bash import ExperimentalGitBash, GitBash
from vibe.core.tools.builtins.read_file import ReadFile
from vibe.core.tools.builtins.web_fetch import WebFetch
from vibe.core.tools.builtins.web_search import WebSearch
from vibe.core.tools.builtins.write_file import WriteFile


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


def test_git_bash_prompt_names_tool_and_disables_bash_alias() -> None:
    description = GitBash.get_full_description()

    assert "named `git_bash`" in description
    assert "no `bash` tool" in description
    assert "POSIX/Git Bash syntax" in description


def test_experimental_git_bash_prompt_names_tool_and_disables_bash_alias() -> None:
    description = ExperimentalGitBash.get_full_description()

    assert "named `git_bash`" in description
    assert "no `bash` tool" in description
    assert "POSIX/Git Bash syntax" in description
