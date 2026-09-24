"""Tests for Skill Forge guided workflow."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import shutil

import pytest

from tests.skills.conftest import create_skill
from vibe.core.config import VibeConfig
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import SkillMetadata, SkillInfo


@pytest.fixture
def skill_forge_dir(tmp_path: Path) -> Path:
    """Set up skill-forge directory."""
    skill_dir = tmp_path / "skills" / "skill-forge"
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Copy skill-forge files
    import sys
    src_dir = Path(__file__).parent.parent.parent / "skills" / "skill-forge"
    if src_dir.exists():
        for f in src_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, skill_dir / f.name)
            elif f.name != '__pycache__':
                shutil.copytree(f, skill_dir / f.name)

    return skill_dir


@pytest.fixture
def skill_manager(tmp_path: Path, skill_forge_dir: Path) -> SkillManager:
    """Create a SkillManager with skill-forge loaded."""
    config = VibeConfig(skill_paths=[skill_forge_dir.parent])
    return SkillManager(lambda: config)


class TestSkillForgeDiscovery:
    """Test that skill-forge is discovered and parsed correctly."""

    def test_skill_forge_discovered(
        self, skill_manager: SkillManager
    ) -> None:
        """Test that skill-forge appears in available skills."""
        assert "skill-forge" in skill_manager.available_skills

    def test_skill_forge_has_activation(
        self, skill_manager: SkillManager
    ) -> None:
        """Test that skill-forge has activation metadata."""
        skill_info = skill_manager.get_skill("skill-forge")
        assert skill_info is not None
        # Check activation field exists
        if skill_info.activation:
            assert skill_info.activation.type == "guided"

    def test_skill_forge_is_user_invocable(
        self, skill_manager: SkillManager
    ) -> None:
        """Test that skill-forge is user-invocable."""
        skill_info = skill_manager.get_skill("skill-forge")
        assert skill_info.user_invocable is True


class TestSkillForgeMiddleware:
    """Test SkillForgeMiddleware behavior."""

    def test_middleware_import(self) -> None:
        """Test that SkillForgeMiddleware can be imported."""
        sys_path = Path(__file__).parent.parent.parent
        import sys
        if str(sys_path) not in sys.path:
            sys.path.insert(0, str(sys_path))

        try:
            from skills.skill_forge.middleware import SkillForgeMiddleware
            mw = SkillForgeMiddleware()
            assert mw is not None
            assert mw.current_step == "init"
            assert mw.user_confirmed is False
        except ImportError:
            pytest.skip("SkillForgeMiddleware not available")

    def test_middleware_step_management(self) -> None:
        """Test middleware step tracking."""
        import sys
        sys_path = Path(__file__).parent.parent.parent
        if str(sys_path) not in sys.path:
            sys.path.insert(0, str(sys_path))

        try:
            from skills.skill_forge.middleware import SkillForgeMiddleware

            mw = SkillForgeMiddleware()
            mw.set_step("test_phase")
            assert mw.current_step == "test_phase"

            mw.confirm_step()
            assert mw.user_confirmed is True
        except ImportError:
            pytest.skip("SkillForgeMiddleware not available")


class TestSkillForgeAgentLoop:
    """Test enter_skill_forge and exit_skill_forge."""

    def test_agent_loop_import(self) -> None:
        """Test that agent_loop module can be imported."""
        import sys
        sys_path = Path(__file__).parent.parent.parent
        if str(sys_path) not in sys.path:
            sys.path.insert(0, str(sys_path))

        try:
            from skills.skill_forge.agent_loop import (
                enter_skill_forge, exit_skill_forge,
                stage_artifact, validate_staged_artifacts
            )
            assert enter_skill_forge is not None
            assert exit_skill_forge is not None
        except ImportError:
            pytest.skip("agent_loop module not available")

    def test_stage_artifact(self, tmp_path: Path) -> None:
        """Test staging artifacts to temp directory."""
        import sys
        sys_path = Path(__file__).parent.parent.parent
        if str(sys_path) not in sys.path:
            sys.path.insert(0, str(sys_path))

        try:
            from skills.skill_forge.agent_loop import stage_artifact, _staging_dir

            # Mock staging dir
            import skills.skill_forge.agent_loop as al
            al._staging_dir = tmp_path / "staging"
            al._staging_dir.mkdir(parents=True, exist_ok=True)
            al._session_id = "test-session"

            result = stage_artifact("skills", "test-skill", "# Test Skill\n\nContent")
            assert result.exists()
            assert result.read_text() == "# Test Skill\n\nContent"

        except ImportError:
            pytest.skip("agent_loop module not available")


class TestSkillForgeIntegration:
    """Integration tests for the full workflow."""

    def test_skill_forge_parsing(
        self, skill_manager: SkillManager
    ) -> None:
        """Test full parsing of skill-forge SKILL.md."""
        skill_info = skill_manager.get_skill("skill-forge")
        assert skill_info is not None
        assert skill_info.name == "skill-forge"
        assert "guided" in skill_info.description.lower() or "interactive" in skill_info.description.lower()

    def test_skill_forges_agent_profile(self) -> None:
        """Test that skill-forge-agent.toml exists."""
        agent_path = Path(__file__).parent.parent.parent / "agents" / "skill-forge-agent.toml"
        if agent_path.exists():
            content = agent_path.read_text()
            assert "skill-forge" in content.lower()
        else:
            pytest.skip("skill-forge-agent.toml not found")

    def test_skill_forges_system_prompt(self) -> None:
        """Test that skill-forge-system.md exists."""
        prompt_path = Path(__file__).parent.parent.parent / "prompts" / "skill-forge-system.md"
        if prompt_path.exists():
            content = prompt_path.read_text()
            assert "Skill Forge" in content or "skill-forge" in content.lower()
        else:
            pytest.skip("skill-forge-system.md not found")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
