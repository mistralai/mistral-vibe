from __future__ import annotations

from vibe.core.scratchpad import get_scratchpad_dir, init_scratchpad, is_scratchpad_path


class TestInitScratchpad:
    def test_creates_directory(self):
        result = init_scratchpad("test-session")
        assert result is not None
        assert result.is_dir()

    def test_idempotent_same_session(self):
        first = init_scratchpad("session-1")
        second = init_scratchpad("session-1")
        assert first == second

    def test_different_sessions_get_different_dirs(self):
        first = init_scratchpad("session-1")
        second = init_scratchpad("session-2")
        assert first != second

    def test_session_id_in_dir_name(self):
        result = init_scratchpad("abcdef123456")
        assert result is not None
        assert "abcdef12" in result.name

    def test_sets_module_state(self):
        init_scratchpad("test-session")
        assert get_scratchpad_dir("test-session") is not None


class TestGetScratchpadDir:
    def test_none_for_unknown_session(self):
        assert get_scratchpad_dir("nonexistent") is None

    def test_returns_path_after_init(self):
        path = init_scratchpad("test-session")
        assert get_scratchpad_dir("test-session") == path


class TestIsScratchpadPath:
    def test_false_when_not_initialized(self):
        assert not is_scratchpad_path("/tmp/anything")

    def test_true_for_file_inside(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        assert is_scratchpad_path(str(sp / "file.txt"))

    def test_true_for_nested_file(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        assert is_scratchpad_path(str(sp / "subdir" / "file.txt"))

    def test_true_for_dir_itself(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        assert is_scratchpad_path(str(sp))

    def test_true_across_sessions(self):
        sp1 = init_scratchpad("session-1")
        sp2 = init_scratchpad("session-2")
        assert sp1 is not None and sp2 is not None
        assert is_scratchpad_path(str(sp1 / "file.txt"))
        assert is_scratchpad_path(str(sp2 / "file.txt"))

    def test_false_for_outside_path(self):
        init_scratchpad("test-session")
        assert not is_scratchpad_path("/etc/passwd")

    def test_false_for_traversal_attack(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        traversal = str(sp / ".." / ".." / ".." / "etc" / "passwd")
        assert not is_scratchpad_path(traversal)

    def test_false_for_sibling_directory(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        sibling = str(sp.parent / "other-dir" / "file.txt")
        assert not is_scratchpad_path(sibling)
