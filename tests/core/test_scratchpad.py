from __future__ import annotations

from vibe.core.scratchpad import cleanup_scratchpad, init_scratchpad, is_scratchpad_path


class TestInitScratchpad:
    def test_creates_directory(self):
        result = init_scratchpad("test-session")
        assert result is not None
        assert result.is_dir()

    def test_different_sessions_get_different_dirs(self):
        first = init_scratchpad("session-1")
        second = init_scratchpad("session-2")
        assert first != second

    def test_session_id_in_dir_name(self):
        result = init_scratchpad("abcdef123456")
        assert result is not None
        assert "abcdef12" in result.name

    def test_cleanup_removes_directory(self):
        path = init_scratchpad("test-session")
        assert path is not None
        cleanup_scratchpad(path)
        assert not path.exists()


class TestIsScratchpadPath:
    def test_false_when_not_initialized(self):
        assert not is_scratchpad_path("/tmp/anything", scratchpad_dir=None)

    def test_true_for_file_inside(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        assert is_scratchpad_path(str(sp / "file.txt"), scratchpad_dir=sp)

    def test_true_for_nested_file(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        assert is_scratchpad_path(str(sp / "subdir" / "file.txt"), scratchpad_dir=sp)

    def test_true_for_dir_itself(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        assert is_scratchpad_path(str(sp), scratchpad_dir=sp)

    def test_other_session_scratchpad_is_not_trusted(self):
        sp1 = init_scratchpad("session-1")
        sp2 = init_scratchpad("session-2")
        assert sp1 is not None and sp2 is not None
        assert is_scratchpad_path(str(sp1 / "file.txt"), scratchpad_dir=sp1)
        assert not is_scratchpad_path(str(sp2 / "file.txt"), scratchpad_dir=sp1)

    def test_false_for_outside_path(self):
        sp = init_scratchpad("test-session")
        assert not is_scratchpad_path("/etc/passwd", scratchpad_dir=sp)

    def test_false_for_traversal_attack(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        traversal = str(sp / ".." / ".." / ".." / "etc" / "passwd")
        assert not is_scratchpad_path(traversal, scratchpad_dir=sp)

    def test_false_for_sibling_directory(self):
        sp = init_scratchpad("test-session")
        assert sp is not None
        sibling = str(sp.parent / "other-dir" / "file.txt")
        assert not is_scratchpad_path(sibling, scratchpad_dir=sp)
