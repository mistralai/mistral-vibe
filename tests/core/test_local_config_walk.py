from __future__ import annotations

from pathlib import Path

from vibe.core.paths._local_config_walk import (
    _MAX_DIRS,
    WALK_MAX_DEPTH,
    walk_local_config_dirs,
)


class TestWalkTools:
    def test_finds_config_at_root(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" / "tools" in result.tools

    def test_finds_config_within_depth_limit(self, tmp_path: Path) -> None:
        nested = tmp_path
        for i in range(WALK_MAX_DEPTH):
            nested = nested / f"level{i}"
        (nested / ".vibe" / "skills").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert nested.resolve() / ".vibe" / "skills" in result.skills

    def test_does_not_find_config_beyond_depth_limit(self, tmp_path: Path) -> None:
        nested = tmp_path
        for i in range(WALK_MAX_DEPTH + 1):
            nested = nested / f"level{i}"
        (nested / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert not result.tools
        assert not result.skills
        assert not result.agents

    def test_respects_dir_count_limit(self, tmp_path: Path) -> None:
        for i in range(_MAX_DIRS + 10):
            (tmp_path / f"dir{i:05d}").mkdir()
        (tmp_path / "zzz_last" / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert isinstance(result.tools, tuple)

    def test_skips_ignored_directories(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules" / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert result.tools == (tmp_path.resolve() / ".vibe" / "tools",)

    def test_skips_dot_directories(self, tmp_path: Path) -> None:
        (tmp_path / ".hidden" / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert not result.tools

    def test_preserves_alphabetical_ordering(self, tmp_path: Path) -> None:
        (tmp_path / "bbb" / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / "aaa" / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        resolved = tmp_path.resolve()
        assert result.tools == (
            resolved / ".vibe" / "tools",
            resolved / "aaa" / ".vibe" / "tools",
            resolved / "bbb" / ".vibe" / "tools",
        )

    def test_finds_agents_skills(self, tmp_path: Path) -> None:
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".agents" / "skills" in result.skills

    def test_finds_all_config_types(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / ".vibe" / "agents").mkdir(parents=True)
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        resolved = tmp_path.resolve()
        assert resolved / ".vibe" / "tools" in result.tools
        assert resolved / ".vibe" / "skills" in result.skills
        assert resolved / ".vibe" / "agents" in result.agents
        assert resolved / ".agents" / "skills" in result.skills


class TestWalkConfigDirs:
    def test_finds_vibe_with_tools(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" in result.config_dirs

    def test_finds_vibe_with_skills(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" in result.config_dirs

    def test_finds_agents_with_skills(self, tmp_path: Path) -> None:
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".agents" in result.config_dirs

    def test_ignores_empty_vibe_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        result = walk_local_config_dirs(tmp_path)
        assert result.config_dirs == ()

    def test_ignores_empty_agents_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".agents").mkdir()
        result = walk_local_config_dirs(tmp_path)
        assert result.config_dirs == ()

    def test_returns_empty_when_empty(self, tmp_path: Path) -> None:
        result = walk_local_config_dirs(tmp_path)
        assert result.config_dirs == ()

    def test_finds_shallow_nested(self, tmp_path: Path) -> None:
        (tmp_path / "sub" / ".vibe" / "skills").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / "sub" / ".vibe" in result.config_dirs

    def test_finds_at_depth_2(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b" / ".agents" / "skills").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / "a" / "b" / ".agents" in result.config_dirs

    def test_returns_empty_beyond_default_depth(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b" / "c" / "d" / "e" / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert result.config_dirs == ()

    def test_custom_depth(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b" / "c" / "d" / "e" / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path, max_depth=5)
        assert (
            tmp_path.resolve() / "a" / "b" / "c" / "d" / "e" / ".vibe"
            in result.config_dirs
        )

    def test_finds_match_among_many_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        for i in range(100):
            (tmp_path / f"dir{i}").mkdir()
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" in result.config_dirs

    def test_skips_ignored_directories(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules" / ".vibe" / "skills").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert result.config_dirs == ()

    def test_finds_vibe_with_prompts(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "prompts").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" in result.config_dirs

    def test_finds_vibe_with_config_toml(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        (tmp_path / ".vibe" / "config.toml").write_text("")
        result = walk_local_config_dirs(tmp_path)
        assert tmp_path.resolve() / ".vibe" in result.config_dirs

    def test_finds_multiple_config_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        (tmp_path / "sub" / ".vibe" / "tools").mkdir(parents=True)
        result = walk_local_config_dirs(tmp_path)
        resolved = tmp_path.resolve()
        assert resolved / ".vibe" in result.config_dirs
        assert resolved / ".agents" in result.config_dirs
        assert resolved / "sub" / ".vibe" in result.config_dirs
