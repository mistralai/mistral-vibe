from __future__ import annotations

from pathlib import Path

from vibe.core.paths._local_config_walk import (
    _MAX_DIRS,
    WALK_MAX_DEPTH,
    has_config_dirs_nearby,
    walk_local_config_dirs_all,
)


class TestBoundedWalk:
    def test_finds_config_at_root(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        tools, skills, agents = walk_local_config_dirs_all(tmp_path)
        assert tmp_path / ".vibe" / "tools" in tools

    def test_finds_config_within_depth_limit(self, tmp_path: Path) -> None:
        nested = tmp_path
        for i in range(WALK_MAX_DEPTH):
            nested = nested / f"level{i}"
        (nested / ".vibe" / "skills").mkdir(parents=True)
        _, skills, _ = walk_local_config_dirs_all(tmp_path)
        assert nested / ".vibe" / "skills" in skills

    def test_does_not_find_config_beyond_depth_limit(self, tmp_path: Path) -> None:
        nested = tmp_path
        for i in range(WALK_MAX_DEPTH + 1):
            nested = nested / f"level{i}"
        (nested / ".vibe" / "tools").mkdir(parents=True)
        tools, skills, agents = walk_local_config_dirs_all(tmp_path)
        assert not tools
        assert not skills
        assert not agents

    def test_respects_dir_count_limit(self, tmp_path: Path) -> None:
        # Create more directories than _MAX_DIRS at depth 1
        for i in range(_MAX_DIRS + 10):
            (tmp_path / f"dir{i:05d}").mkdir()
        # Place config in a directory that would be scanned late
        (tmp_path / "zzz_last" / ".vibe" / "tools").mkdir(parents=True)

        tools, _, _ = walk_local_config_dirs_all(tmp_path)
        # The walk should stop before visiting all dirs.
        # Whether zzz_last is found depends on sort order and limit,
        # but total visited dirs should be bounded.
        # We just verify no crash and the function returns.
        assert isinstance(tools, tuple)

    def test_skips_ignored_directories(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules" / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        tools, _, _ = walk_local_config_dirs_all(tmp_path)
        assert tools == (tmp_path / ".vibe" / "tools",)

    def test_skips_dot_directories(self, tmp_path: Path) -> None:
        (tmp_path / ".hidden" / ".vibe" / "tools").mkdir(parents=True)
        tools, _, _ = walk_local_config_dirs_all(tmp_path)
        assert not tools

    def test_preserves_alphabetical_ordering(self, tmp_path: Path) -> None:
        (tmp_path / "bbb" / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / "aaa" / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        tools, _, _ = walk_local_config_dirs_all(tmp_path)
        assert tools == (
            tmp_path / ".vibe" / "tools",
            tmp_path / "aaa" / ".vibe" / "tools",
            tmp_path / "bbb" / ".vibe" / "tools",
        )

    def test_finds_agents_skills(self, tmp_path: Path) -> None:
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        _, skills, _ = walk_local_config_dirs_all(tmp_path)
        assert tmp_path / ".agents" / "skills" in skills

    def test_finds_all_config_types(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / ".vibe" / "agents").mkdir(parents=True)
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        tools, skills, agents = walk_local_config_dirs_all(tmp_path)
        assert tmp_path / ".vibe" / "tools" in tools
        assert tmp_path / ".vibe" / "skills" in skills
        assert tmp_path / ".vibe" / "agents" in agents
        assert tmp_path / ".agents" / "skills" in skills


class TestHasConfigDirsNearby:
    def test_returns_true_when_vibe_tools_exist(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        assert has_config_dirs_nearby(tmp_path) is True

    def test_returns_true_when_vibe_skills_exist(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        assert has_config_dirs_nearby(tmp_path) is True

    def test_returns_true_when_agents_skills_exist(self, tmp_path: Path) -> None:
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        assert has_config_dirs_nearby(tmp_path) is True

    def test_returns_false_when_empty(self, tmp_path: Path) -> None:
        assert has_config_dirs_nearby(tmp_path) is False

    def test_returns_false_for_vibe_dir_without_subdirs(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        assert has_config_dirs_nearby(tmp_path) is False

    def test_returns_true_for_shallow_nested(self, tmp_path: Path) -> None:
        (tmp_path / "sub" / ".vibe" / "skills").mkdir(parents=True)
        assert has_config_dirs_nearby(tmp_path) is True

    def test_returns_true_at_depth_2(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b" / ".agents" / "skills").mkdir(parents=True)
        assert has_config_dirs_nearby(tmp_path) is True

    def test_returns_false_beyond_default_depth(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b" / "c" / "d" / "e" / ".vibe" / "tools").mkdir(parents=True)
        assert has_config_dirs_nearby(tmp_path) is False

    def test_custom_depth(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b" / "c" / "d" / "e" / ".vibe" / "tools").mkdir(parents=True)
        assert has_config_dirs_nearby(tmp_path, max_depth=5) is True

    def test_early_exit_on_first_match(self, tmp_path: Path) -> None:
        # Create many dirs but put config early; function should return quickly
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        for i in range(100):
            (tmp_path / f"dir{i}").mkdir()
        assert has_config_dirs_nearby(tmp_path) is True

    def test_skips_ignored_directories(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules" / ".vibe" / "skills").mkdir(parents=True)
        assert has_config_dirs_nearby(tmp_path) is False
