"""Tests for the hybrid `/init` prompt builder.

The builder runs a deterministic scan and wraps the facts in instructions for the
agent to author AGENTS.md. These tests cover the three branches the prompt takes:
empty project, project with detectable facts, and an existing AGENTS.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibe.setup.init import build_init_prompt


@pytest.mark.asyncio
async def test_prompt_includes_scanned_facts(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo-app"\nversion = "1.2.3"\n'
        'description = "A demo"\n',
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")

    prompt = await build_init_prompt(tmp_path)

    assert "Scan facts:" in prompt
    assert "demo-app" in prompt
    assert "Python" in prompt
    # No AGENTS.md present -> create branch, not the improve branch.
    assert "No AGENTS.md exists yet" in prompt
    assert "already exists" not in prompt
    # Always tells the agent to verify rather than trust the scan.
    assert "Verify" in prompt or "verify" in prompt


@pytest.mark.asyncio
async def test_prompt_handles_empty_project(tmp_path: Path) -> None:
    prompt = await build_init_prompt(tmp_path)

    assert "No AGENTS.md exists yet" in prompt
    # Falls back gracefully when the scan finds nothing.
    assert "none detected" in prompt


@pytest.mark.asyncio
async def test_prompt_uses_improve_branch_when_agents_md_exists(
    tmp_path: Path,
) -> None:
    existing = "# Existing Guide\n\nRun `make test`.\n"
    (tmp_path / "AGENTS.md").write_text(existing, encoding="utf-8")

    prompt = await build_init_prompt(tmp_path)

    assert "already exists" in prompt
    assert "Do not rewrite it wholesale" in prompt
    # The current file is embedded so the agent can improve it in place.
    assert "Existing AGENTS.md:" in prompt
    assert "Run `make test`." in prompt


@pytest.mark.asyncio
async def test_prompt_finds_agents_md_in_vibe_dir(tmp_path: Path) -> None:
    vibe_dir = tmp_path / ".vibe"
    vibe_dir.mkdir()
    (vibe_dir / "AGENTS.md").write_text("# Vibe-scoped guide\n", encoding="utf-8")

    prompt = await build_init_prompt(tmp_path)

    assert "already exists" in prompt
    assert "Vibe-scoped guide" in prompt


@pytest.mark.asyncio
async def test_does_not_write_any_file(tmp_path: Path) -> None:
    """The builder only reads; the agent writes AGENTS.md during its turn."""
    before = set(tmp_path.iterdir())

    await build_init_prompt(tmp_path)

    assert set(tmp_path.iterdir()) == before
