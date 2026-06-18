from __future__ import annotations

import json

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.notebook_edit import (
    NotebookEdit,
    NotebookEditArgs,
    NotebookEditConfig,
)


@pytest.fixture
def notebook_edit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return NotebookEdit(
        config_getter=lambda: NotebookEditConfig(), state=BaseToolState()
    )


def _write_notebook(tmp_path, cells):
    nb = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    path = tmp_path / "nb.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    return path


def _code_cell(src):
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [src],
    }


@pytest.mark.asyncio
async def test_replace_cell_source(notebook_edit, tmp_path):
    path = _write_notebook(tmp_path, [_code_cell("x = 1\n")])

    result = await collect_result(
        notebook_edit.run(
            NotebookEditArgs(
                notebook_path=str(path), cell_index=0, new_source="x = 2\n"
            )
        )
    )

    assert result.cell_count == 1
    nb = json.loads(path.read_text())
    assert "x = 2\n" in "".join(nb["cells"][0]["source"])


@pytest.mark.asyncio
async def test_insert_cell(notebook_edit, tmp_path):
    path = _write_notebook(tmp_path, [_code_cell("a = 1\n")])

    result = await collect_result(
        notebook_edit.run(
            NotebookEditArgs(
                notebook_path=str(path),
                cell_index=0,
                new_source="b = 2\n",
                edit_mode="insert",
            )
        )
    )

    assert result.cell_count == 2
    nb = json.loads(path.read_text())
    assert "".join(nb["cells"][0]["source"]) == "b = 2\n"


@pytest.mark.asyncio
async def test_delete_cell(notebook_edit, tmp_path):
    path = _write_notebook(tmp_path, [_code_cell("a\n"), _code_cell("b\n")])

    result = await collect_result(
        notebook_edit.run(
            NotebookEditArgs(notebook_path=str(path), cell_index=0, edit_mode="delete")
        )
    )

    assert result.cell_count == 1
    nb = json.loads(path.read_text())
    assert "".join(nb["cells"][0]["source"]) == "b\n"


@pytest.mark.asyncio
async def test_insert_markdown_cell_type(notebook_edit, tmp_path):
    path = _write_notebook(tmp_path, [_code_cell("a\n")])

    await collect_result(
        notebook_edit.run(
            NotebookEditArgs(
                notebook_path=str(path),
                cell_index=1,
                new_source="# Title\n",
                cell_type="markdown",
                edit_mode="insert",
            )
        )
    )

    nb = json.loads(path.read_text())
    assert nb["cells"][1]["cell_type"] == "markdown"
    assert "outputs" not in nb["cells"][1]


@pytest.mark.asyncio
async def test_out_of_range_raises(notebook_edit, tmp_path):
    path = _write_notebook(tmp_path, [_code_cell("a\n")])

    with pytest.raises(ToolError):
        await collect_result(
            notebook_edit.run(
                NotebookEditArgs(
                    notebook_path=str(path), cell_index=5, new_source="x\n"
                )
            )
        )


@pytest.mark.asyncio
async def test_non_ipynb_raises(notebook_edit, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not a notebook")

    with pytest.raises(ToolError):
        await collect_result(
            notebook_edit.run(
                NotebookEditArgs(
                    notebook_path=str(path), cell_index=0, new_source="x\n"
                )
            )
        )


@pytest.mark.asyncio
async def test_invalid_json_raises(notebook_edit, tmp_path):
    path = tmp_path / "broken.ipynb"
    path.write_text("{ not json")

    with pytest.raises(ToolError):
        await collect_result(
            notebook_edit.run(
                NotebookEditArgs(
                    notebook_path=str(path), cell_index=0, new_source="x\n"
                )
            )
        )


def test_tool_name_is_notebook_edit():
    assert NotebookEdit.get_name() == "notebook_edit"
