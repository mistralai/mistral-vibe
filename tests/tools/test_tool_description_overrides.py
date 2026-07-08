"""Tool descriptions live in ``<tools-dir>/prompts/<name>.md`` — the same layout
the builtins use (``builtins/*.py`` + ``builtins/prompts/*.md``). A file there
describes a custom tool or overrides a builtin/MCP tool of that name.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.llm.format import APIToolFormatHandler
from vibe.core.tools.builtins.bash import Bash
from vibe.core.tools.manager import ToolManager
from vibe.core.trusted_folders import find_trustable_files, trusted_folders_manager


def _prompts_dir(root: Path) -> Path:
    """`<root>/tools/prompts/`, created."""
    prompts = root / "tools" / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    return prompts


def _descriptions(manager: ToolManager) -> dict[str, str]:
    """Map tool name -> the description the model would see."""
    return {fn.name: fn.description for fn in manager.available_tool_specs()}


def _write_custom_tool(tool_dir: Path, file_stem: str, class_name: str) -> None:
    """Write a minimal custom tool ``<file_stem>.py`` defining ``class_name``."""
    (tool_dir / f"{file_stem}.py").write_text(
        f"""
from collections.abc import AsyncGenerator

from pydantic import BaseModel

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState


class _Args(BaseModel):
    pass


class _Result(BaseModel):
    pass


class {class_name}(BaseTool[_Args, _Result, BaseToolConfig, BaseToolState]):
    description = "Inline {class_name} description"

    async def run(self, args: _Args, ctx=None) -> AsyncGenerator[_Result, None]:
        yield _Result()
""",
        encoding="utf-8",
    )
    for mod in [k for k in sys.modules if file_stem in k]:
        del sys.modules[mod]


def test_prompts_md_overrides_builtin_description(tmp_path: Path) -> None:
    (_prompts_dir(tmp_path) / "bash.md").write_text(
        "Custom bash description.", encoding="utf-8"
    )
    config = build_test_vibe_config(tool_paths=[str(tmp_path / "tools")])
    manager = ToolManager(lambda: config)

    assert _descriptions(manager)["bash"] == "Custom bash description."


def test_falls_back_to_builtin_description_when_no_override(tmp_path: Path) -> None:
    config = build_test_vibe_config(tool_paths=[str(tmp_path)])
    manager = ToolManager(lambda: config)

    descriptions = _descriptions(manager)
    assert descriptions["bash"] == Bash.get_full_description()
    assert descriptions["bash"].startswith("Use `bash`")


def test_empty_override_file_is_ignored(tmp_path: Path) -> None:
    (_prompts_dir(tmp_path) / "bash.md").write_text("   \n\t", encoding="utf-8")
    config = build_test_vibe_config(tool_paths=[str(tmp_path / "tools")])
    manager = ToolManager(lambda: config)

    assert _descriptions(manager)["bash"] == Bash.get_full_description()


def test_later_search_path_wins(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    (_prompts_dir(first)).joinpath("bash.md").write_text(
        "From first.", encoding="utf-8"
    )
    (_prompts_dir(second)).joinpath("bash.md").write_text(
        "From second.", encoding="utf-8"
    )

    config = build_test_vibe_config(
        tool_paths=[str(first / "tools"), str(second / "tools")]
    )
    manager = ToolManager(lambda: config)

    # Later search paths win, matching how duplicate `.py` tools resolve.
    assert _descriptions(manager)["bash"] == "From second."


def test_override_flows_through_available_tools(tmp_path: Path) -> None:
    (_prompts_dir(tmp_path) / "bash.md").write_text(
        "Custom bash description.", encoding="utf-8"
    )
    config = build_test_vibe_config(tool_paths=[str(tmp_path / "tools")])
    manager = ToolManager(lambda: config)

    available = APIToolFormatHandler().get_available_tools(manager)
    bash_tool = next(t for t in available if t.function.name == "bash")

    assert bash_tool.function.description == "Custom bash description."


def test_override_from_project_vibe_tools_dir(
    tmp_working_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovered through the harness-managed project tools dir
    (``.vibe/tools/prompts/``), not just explicit ``tool_paths``.
    """
    monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
    prompts_dir = tmp_working_directory / ".vibe" / "tools" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "bash.md").write_text("Project bash description.", encoding="utf-8")

    config = build_test_vibe_config()
    manager = ToolManager(lambda: config)

    assert _descriptions(manager)["bash"] == "Project bash description."


def test_custom_tool_description_from_prompts(tmp_path: Path) -> None:
    """A custom tool's description lives beside it in ``tools/prompts/<name>.md``
    — the same convention as an override.
    """
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    _write_custom_tool(tool_dir, file_stem="weather", class_name="Weather")
    (_prompts_dir(tmp_path) / "weather.md").write_text(
        "Forecast the weather.", encoding="utf-8"
    )

    config = build_test_vibe_config(tool_paths=[str(tool_dir)])
    manager = ToolManager(lambda: config)

    assert _descriptions(manager)["weather"] == "Forecast the weather."


def test_override_for_tool_from_file_tool_path_entry(tmp_path: Path) -> None:
    """A ``tool_paths`` entry can point at a single ``.py`` file; the ``prompts/``
    dir beside it supplies the description — mirroring how ``_iter_tool_classes``
    loads a tool from a file entry.
    """
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    _write_custom_tool(tool_dir, file_stem="weather", class_name="Weather")
    (_prompts_dir(tmp_path) / "weather.md").write_text(
        "Forecast the weather.", encoding="utf-8"
    )

    config = build_test_vibe_config(tool_paths=[str(tool_dir / "weather.py")])
    manager = ToolManager(lambda: config)

    assert _descriptions(manager)["weather"] == "Forecast the weather."


def test_tools_dir_with_prompts_triggers_trust_warning(tmp_path: Path) -> None:
    """A ``.vibe/tools/`` holding only a description override still surfaces in
    the trust-directory warning.
    """
    prompts_dir = tmp_path / ".vibe" / "tools" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "bash.md").write_text("Custom bash description.", encoding="utf-8")

    assert ".vibe/" in find_trustable_files(tmp_path)
