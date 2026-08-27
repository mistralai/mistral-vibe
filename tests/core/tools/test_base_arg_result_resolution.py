from __future__ import annotations

import pytest

from vibe.core.tools.builtins.bash import CapturedShellResult
from vibe.core.tools.builtins.experimental_bash import (
    ExperimentalBashArgs,
    ExperimentalBashResult,
)
from vibe.core.tools.builtins.git_bash import ExperimentalGitBash, GitBash, GitBashArgs
from vibe.core.tools.builtins.windows_shell import (
    ExperimentalWindowsShell,
    WindowsShell,
    WindowsShellArgs,
)


@pytest.mark.parametrize("tool_class", [ExperimentalGitBash, ExperimentalWindowsShell])
def test_inherited_run_annotation_resolves_against_the_defining_module(tool_class):
    # These subclasses live in another module than the `run` they inherit, so
    # the annotation only resolves if it is looked up where it was written.
    assert tool_class._get_tool_args_results() == (
        ExperimentalBashArgs,
        ExperimentalBashResult,
    )


@pytest.mark.parametrize(
    ("tool_class", "args_model"),
    [(GitBash, GitBashArgs), (WindowsShell, WindowsShellArgs)],
)
def test_fallback_shells_declare_their_own_result_model(tool_class, args_model):
    assert tool_class._get_tool_args_results() == (args_model, CapturedShellResult)
