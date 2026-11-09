"""Tool descriptions live in ``<tools-dir>/prompts/<name>.md`` — the same layout
the builtins use (``builtins/*.py`` + ``builtins/prompts/*.md``). A file there
describes a custom tool or overrides a builtin/MCP tool of that name.
"""

from __future__ import annotations

from pathlib import Path

from vibe.core.trusted_folders import find_trustable_files


def test_tools_dir_with_prompts_triggers_trust_warning(tmp_path: Path) -> None:
    """A ``.vibe/tools/`` holding only a description override still surfaces in
    the trust-directory warning.
    """
    prompts_dir = tmp_path / ".vibe" / "tools" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "bash.md").write_text("Custom bash description.", encoding="utf-8")

    assert ".vibe/" in find_trustable_files(tmp_path)
