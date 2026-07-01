"""Tests for the `/init` prompt builder.

`/init` is a single agent turn: the builder only assembles instructions telling
the agent to explore the repo and author AGENTS.md itself. These tests cover the
two branches the prompt takes (create vs. improve) and confirm the builder never
writes to disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibe.setup.init import build_init_prompt


@pytest.mark.asyncio
async def test_prompt_instructs_create_when_no_agents_md(tmp_path: Path) -> None:
    prompt = await build_init_prompt(tmp_path)

    assert "No AGENTS.md exists yet" in prompt
    assert "already exists" not in prompt
    # Tells the agent to explore and confirm rather than guess.
    assert "Explore the repo" in prompt
    assert "confirm" in prompt or "Confirm" in prompt


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
