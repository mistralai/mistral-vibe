from __future__ import annotations

from pathlib import Path

from vibe.core.paths._local_config_files import LocalConfigDirs, find_local_config_dirs


class TestSubdirs:
    def test_finds_config_at_root(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        result = find_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" / "tools" in result.tools

    def test_does_not_descend_into_subdirectories(self, tmp_path: Path) -> None:
        (tmp_path / "sub" / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / "a" / "b" / ".vibe" / "skills").mkdir(parents=True)
        result = find_local_config_dirs(tmp_path)
        assert result.tools == ()
        assert result.skills == ()
        assert result.agents == ()
        assert result.config_dirs == ()

    def test_finds_agents_skills_at_root(self, tmp_path: Path) -> None:
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        result = find_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".agents" / "skills" in result.skills

    def test_finds_all_config_types_at_root(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / ".vibe" / "agents").mkdir(parents=True)
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        result = find_local_config_dirs(tmp_path)
        resolved = tmp_path.resolve()
        assert resolved / ".vibe" / "tools" in result.tools
        assert resolved / ".vibe" / "skills" in result.skills
        assert resolved / ".vibe" / "agents" in result.agents
        assert resolved / ".agents" / "skills" in result.skills


class TestConfigDirs:
    def test_finds_vibe_with_tools(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        result = find_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" in result.config_dirs

    def test_finds_vibe_with_skills(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        result = find_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" in result.config_dirs

    def test_finds_agents_with_skills(self, tmp_path: Path) -> None:
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        result = find_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".agents" in result.config_dirs

    def test_ignores_empty_vibe_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        result = find_local_config_dirs(tmp_path)
        assert result.config_dirs == ()

    def test_ignores_empty_agents_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".agents").mkdir()
        result = find_local_config_dirs(tmp_path)
        assert result.config_dirs == ()

    def test_returns_empty_when_empty(self, tmp_path: Path) -> None:
        result = find_local_config_dirs(tmp_path)
        assert result.config_dirs == ()

    def test_finds_vibe_with_prompts(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "prompts").mkdir(parents=True)
        result = find_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" in result.config_dirs

    def test_finds_vibe_with_config_toml(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        (tmp_path / ".vibe" / "config.toml").write_text("")
        result = find_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" in result.config_dirs

    def test_finds_vibe_and_agents_at_same_root(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        result = find_local_config_dirs(tmp_path)
        resolved = tmp_path.resolve()
        assert resolved / ".vibe" in result.config_dirs
        assert resolved / ".agents" in result.config_dirs


class TestLocalConfigDirsOr:
    def test_or_concatenates_each_field(self) -> None:
        a = LocalConfigDirs(
            config_dirs=(Path("/a/.vibe"),),
            tools=(Path("/a/.vibe/tools"),),
            skills=(Path("/a/.vibe/skills"),),
            agents=(Path("/a/.vibe/agents"),),
        )
        b = LocalConfigDirs(
            config_dirs=(Path("/b/.vibe"),),
            tools=(Path("/b/.vibe/tools"),),
            skills=(Path("/b/.vibe/skills"),),
            agents=(Path("/b/.vibe/agents"),),
        )
        merged = a | b
        assert merged.config_dirs == (Path("/a/.vibe"), Path("/b/.vibe"))
        assert merged.tools == (Path("/a/.vibe/tools"), Path("/b/.vibe/tools"))
        assert merged.skills == (Path("/a/.vibe/skills"), Path("/b/.vibe/skills"))
        assert merged.agents == (Path("/a/.vibe/agents"), Path("/b/.vibe/agents"))

    def test_or_with_empty_is_identity(self) -> None:
        a = LocalConfigDirs(tools=(Path("/a/.vibe/tools"),))
        assert (a | LocalConfigDirs()) == a
        assert (LocalConfigDirs() | a) == a
