from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from vibe.core.config import VibeConfig
from vibe.core.system_prompt import get_universal_system_prompt, _load_agent_md
from vibe.core.tools.manager import ToolManager


def test_load_agent_md_file_found():
    """Test that _load_agent_md correctly loads content when file exists."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        agent_md_path = temp_path / "AGENT.md"
        agent_md_content = "# My Project Conventions\n\n- Use 4 spaces for indentation\n- Use single quotes for strings"
        agent_md_path.write_text(agent_md_content)

        result = _load_agent_md(temp_path, 10000, enabled=True)
        assert result == agent_md_content


def test_load_agent_md_file_not_found():
    """Test that _load_agent_md returns empty string when file doesn't exist."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        result = _load_agent_md(temp_path, 10000, enabled=True)
        assert result == ""


def test_load_agent_md_disabled():
    """Test that _load_agent_md returns empty string when disabled."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        agent_md_path = temp_path / "AGENT.md"
        agent_md_path.write_text("# My Project Conventions")

        result = _load_agent_md(temp_path, 10000, enabled=False)
        assert result == ""


def test_load_agent_md_lowercase_filename():
    """Test that _load_agent_md can load lowercase agent.md files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        agent_md_path = temp_path / "agent.md"
        agent_md_content = "# My Project Conventions"
        agent_md_path.write_text(agent_md_content)

        result = _load_agent_md(temp_path, 10000, enabled=True)
        assert result == agent_md_content


def test_load_agent_md_custom_filename():
    """Test that _load_agent_md can load custom filename when specified."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        custom_agent_md_path = temp_path / "custom_agent.md"
        agent_md_content = "# My Custom Conventions"
        custom_agent_md_path.write_text(agent_md_content)

        # When a custom filename is provided, it should be tried first
        result = _load_agent_md(temp_path, 10000, enabled=True, filename="custom_agent.md")
        assert result == agent_md_content


def test_agent_md_integration_enabled():
    """Test that AGENT.md content is included in the universal system prompt when enabled."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        agent_md_path = temp_path / "AGENT.md"
        agent_md_content = "# My Project Conventions\n\n- Use 4 spaces for indentation"
        agent_md_path.write_text(agent_md_content)

        config = VibeConfig(
            system_prompt_id="tests",
            include_project_context=True,
            include_prompt_detail=False,
            include_model_info=False,
            workdir=temp_path,
            agent_md={"enabled": True, "filename": "AGENT.md"}
        )
        tool_manager = ToolManager(config)

        prompt = get_universal_system_prompt(tool_manager, config)

        assert "## Project Coding Conventions (from AGENT.md)" in prompt
        assert "# My Project Conventions" in prompt
        assert "- Use 4 spaces for indentation" in prompt


def test_agent_md_integration_disabled():
    """Test that AGENT.md content is not included in the universal system prompt when disabled."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        agent_md_path = temp_path / "AGENT.md"
        agent_md_content = "# My Project Conventions"
        agent_md_path.write_text(agent_md_content)

        config = VibeConfig(
            system_prompt_id="tests",
            include_project_context=True,
            include_prompt_detail=False,
            include_model_info=False,
            workdir=temp_path,
            agent_md={"enabled": False, "filename": "AGENT.md"}
        )
        tool_manager = ToolManager(config)

        prompt = get_universal_system_prompt(tool_manager, config)

        assert "## Project Coding Conventions (from AGENT.md)" not in prompt
        assert "# My Project Conventions" not in prompt


def test_agent_md_integration_default_enabled():
    """Test that AGENT.md content is included by default when enabled is not set."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        agent_md_path = temp_path / "AGENT.md"
        agent_md_content = "# My Project Conventions"
        agent_md_path.write_text(agent_md_content)

        config = VibeConfig(
            system_prompt_id="tests",
            include_project_context=True,
            include_prompt_detail=False,
            include_model_info=False,
            workdir=temp_path
        )
        tool_manager = ToolManager(config)

        prompt = get_universal_system_prompt(tool_manager, config)

        assert "## Project Coding Conventions (from AGENT.md)" in prompt
        assert "# My Project Conventions" in prompt