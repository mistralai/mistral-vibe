from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError
import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.edit import EditArgs
from vibe.core.tools.builtins.experimental_bash import ExperimentalBashArgs
from vibe.core.tools.builtins.git_bash import GitBashArgs
from vibe.core.tools.builtins.grep import GrepArgs
from vibe.core.tools.builtins.read_file import ReadFileArgs
from vibe.core.tools.builtins.windows_shell import WindowsShellArgs
from vibe.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileConfig,
)
from vibe.utils import paths

MSYS_PATH = "/c/Users/acmedev/test/notes.md"
WINDOWS_PATH = "C:/Users/acmedev/test/notes.md"
DRIVELESS_PATH = "/Users/acmedev/test/notes.md"

PATH_ARGUMENTS: list[tuple[type[BaseModel], str, dict[str, str]]] = [
    (WriteFileArgs, "file_path", {"content": "hello"}),
    (EditArgs, "file_path", {"old_string": "a", "new_string": "b"}),
    (ReadFileArgs, "file_path", {}),
    (GrepArgs, "path", {"pattern": "needle"}),
    (ExperimentalBashArgs, "cwd", {"command": "ls"}),
    (GitBashArgs, "cwd", {"command": "ls"}),
    (WindowsShellArgs, "cwd", {"command": "Get-Location"}),
]
PATH_ARGUMENT_IDS = [model.__name__ for model, _, _ in PATH_ARGUMENTS]


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: True)


@pytest.mark.parametrize(
    ("model", "field", "extra"), PATH_ARGUMENTS, ids=PATH_ARGUMENT_IDS
)
@pytest.mark.usefixtures("on_windows")
def test_path_argument_converts_msys_drive_path(
    model: type[BaseModel], field: str, extra: dict[str, str]
) -> None:
    args = model.model_validate({field: MSYS_PATH, **extra})

    assert getattr(args, field) == WINDOWS_PATH


@pytest.mark.parametrize(
    ("model", "field", "extra"), PATH_ARGUMENTS, ids=PATH_ARGUMENT_IDS
)
@pytest.mark.usefixtures("on_windows")
def test_path_argument_rejects_driveless_rooted_path(
    model: type[BaseModel], field: str, extra: dict[str, str]
) -> None:
    with pytest.raises(ValidationError, match="drive"):
        model.model_validate({field: DRIVELESS_PATH, **extra})


@pytest.mark.parametrize(
    ("model", "field", "extra"), PATH_ARGUMENTS, ids=PATH_ARGUMENT_IDS
)
@pytest.mark.parametrize("raw", [MSYS_PATH, DRIVELESS_PATH])
def test_path_argument_is_untouched_off_windows(
    model: type[BaseModel],
    field: str,
    extra: dict[str, str],
    raw: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: False)

    args = model.model_validate({field: raw, **extra})

    assert getattr(args, field) == raw


@pytest.mark.asyncio
@pytest.mark.usefixtures("on_windows")
async def test_driveless_path_fails_the_tool_call_without_creating_anything(
    tmp_working_directory: Path,
) -> None:
    tool = WriteFile(lambda: WriteFileConfig(), BaseToolState())

    with pytest.raises(ToolError, match="drive"):
        await collect_result(tool.invoke(file_path=DRIVELESS_PATH, content="hello"))

    assert not (tmp_working_directory / "Users").exists()
