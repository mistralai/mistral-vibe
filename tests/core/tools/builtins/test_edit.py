from __future__ import annotations

from pathlib import Path

from vibe.core.tools.builtins.edit import EditResult


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def test_ui_hints_not_part_of_model_contract() -> None:
    result = EditResult(file="/x", message="m", old_string="a", new_string="b")
    result._ui_occurrences = [(42, "a", "b")]

    assert result.ui_start_lines == [42]
    assert result.ui_occurrences == [(42, "a", "b")]
    for key in ("ui_start_lines", "ui_occurrences", "_ui_occurrences"):
        assert key not in result.model_dump()
        assert key not in result.model_dump_json()
        assert key not in dict(result)
    assert "ui_occurrences" not in EditResult.model_fields
    assert "ui_occurrences" not in EditResult.model_json_schema().get("properties", {})


def test_ui_occurrences_fall_back_to_snippet_when_unset() -> None:
    result = EditResult(file="/x", message="m", old_string="a", new_string="b")

    assert result.ui_occurrences == [(None, "a", "b")]
