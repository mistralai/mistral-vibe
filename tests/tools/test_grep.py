from __future__ import annotations

import shutil

import pytest

from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.grep import (
    Grep,
    GrepArgs,
    GrepBackend,
    GrepState,
    GrepToolConfig,
)


@pytest.fixture
def grep(tmp_path):
    config = GrepToolConfig(workdir=tmp_path)
    return Grep(config=config, state=GrepState())


@pytest.fixture
def grep_gnu_only(tmp_path, monkeypatch):
    original_which = shutil.which

    def mock_which(cmd):
        if cmd == "rg":
            return None
        return original_which(cmd)

    monkeypatch.setattr("shutil.which", mock_which)
    config = GrepToolConfig(workdir=tmp_path)
    return Grep(config=config, state=GrepState())


def test_detects_ripgrep_when_available(grep):
    if shutil.which("rg"):
        assert grep._detect_backend() == GrepBackend.RIPGREP


def test_falls_back_to_gnu_grep(grep, monkeypatch):
    original_which = shutil.which

    def mock_which(cmd):
        if cmd == "rg":
            return None
        return original_which(cmd)

    monkeypatch.setattr("shutil.which", mock_which)

    if shutil.which("grep"):
        assert grep._detect_backend() == GrepBackend.GNU_GREP


def test_raises_error_if_no_grep_available(grep, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    with pytest.raises(ToolError) as err:
        grep._detect_backend()

    assert "Neither ripgrep (rg) nor grep is installed" in str(err.value)


@pytest.mark.asyncio
async def test_finds_pattern_in_file(grep, tmp_path):
    (tmp_path / "test.py").write_text("def hello():\n    print('world')\n")

    result = await grep.run(GrepArgs(pattern="hello"))

    assert result.match_count == 1
    assert "hello" in result.matches
    assert "test.py" in result.matches
    assert not result.was_truncated


@pytest.mark.asyncio
async def test_finds_multiple_matches(grep, tmp_path):
    (tmp_path / "test.py").write_text("foo\nbar\nfoo\nbaz\nfoo\n")

    result = await grep.run(GrepArgs(pattern="foo"))

    assert result.match_count == 3
    assert result.matches.count("foo") == 3
    assert not result.was_truncated


@pytest.mark.asyncio
async def test_returns_empty_on_no_matches(grep, tmp_path):
    (tmp_path / "test.py").write_text("def hello():\n    pass\n")

    result = await grep.run(GrepArgs(pattern="nonexistent"))

    assert result.match_count == 0
    assert result.matches == ""
    assert not result.was_truncated


@pytest.mark.asyncio
async def test_fails_with_empty_pattern(grep):
    with pytest.raises(ToolError) as err:
        await grep.run(GrepArgs(pattern=""))

    assert "Empty search pattern" in str(err.value)


@pytest.mark.asyncio
async def test_fails_with_nonexistent_path(grep):
    with pytest.raises(ToolError) as err:
        await grep.run(GrepArgs(pattern="test", path="nonexistent"))

    assert "Path does not exist" in str(err.value)


@pytest.mark.asyncio
async def test_searches_in_specific_path(grep, tmp_path):
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "test.py").write_text("match here\n")
    (tmp_path / "other.py").write_text("match here too\n")

    result = await grep.run(GrepArgs(pattern="match", path="subdir"))

    assert result.match_count == 1
    assert "subdir" in result.matches and "test.py" in result.matches
    assert "other.py" not in result.matches


@pytest.mark.asyncio
async def test_truncates_to_max_matches(grep, tmp_path):
    (tmp_path / "test.py").write_text("\n".join(f"line {i}" for i in range(200)))

    result = await grep.run(GrepArgs(pattern="line", max_matches=50))

    assert result.match_count == 50
    assert result.was_truncated


@pytest.mark.asyncio
async def test_truncates_to_max_output_bytes(grep, tmp_path):
    config = GrepToolConfig(workdir=tmp_path, max_output_bytes=100)
    grep_tool = Grep(config=config, state=GrepState())
    (tmp_path / "test.py").write_text("\n".join("x" * 100 for _ in range(10)))

    result = await grep_tool.run(GrepArgs(pattern="x"))

    assert len(result.matches) <= 100
    assert result.was_truncated


@pytest.mark.asyncio
async def test_respects_default_ignore_patterns(grep, tmp_path):
    (tmp_path / "included.py").write_text("match\n")
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "excluded.js").write_text("match\n")

    result = await grep.run(GrepArgs(pattern="match"))

    assert "included.py" in result.matches
    assert "excluded.js" not in result.matches


@pytest.mark.asyncio
async def test_respects_vibeignore_file(grep, tmp_path):
    (tmp_path / ".vibeignore").write_text("custom_dir/\n*.tmp\n")
    custom_dir = tmp_path / "custom_dir"
    custom_dir.mkdir()
    (custom_dir / "excluded.py").write_text("match\n")
    (tmp_path / "excluded.tmp").write_text("match\n")
    (tmp_path / "included.py").write_text("match\n")

    result = await grep.run(GrepArgs(pattern="match"))

    assert "included.py" in result.matches
    assert "excluded.py" not in result.matches
    assert "excluded.tmp" not in result.matches


@pytest.mark.asyncio
async def test_ignores_comments_in_vibeignore(grep, tmp_path):
    (tmp_path / ".vibeignore").write_text("# comment\npattern/\n# another comment\n")
    (tmp_path / "file.py").write_text("match\n")

    result = await grep.run(GrepArgs(pattern="match"))

    assert result.match_count >= 1


@pytest.mark.asyncio
async def test_tracks_search_history(grep, tmp_path):
    (tmp_path / "test.py").write_text("content\n")

    await grep.run(GrepArgs(pattern="first"))
    await grep.run(GrepArgs(pattern="second"))
    await grep.run(GrepArgs(pattern="third"))

    assert grep.state.search_history == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_uses_effective_workdir(tmp_path):
    config = GrepToolConfig(workdir=tmp_path)
    grep_tool = Grep(config=config, state=GrepState())
    (tmp_path / "test.py").write_text("match\n")

    result = await grep_tool.run(GrepArgs(pattern="match", path="."))

    assert result.match_count == 1
    assert "test.py" in result.matches


@pytest.mark.skipif(not shutil.which("grep"), reason="GNU grep not available")
class TestGnuGrepBackend:
    @pytest.mark.asyncio
    async def test_finds_pattern_in_file(self, grep_gnu_only, tmp_path):
        (tmp_path / "test.py").write_text("def hello():\n    print('world')\n")

        result = await grep_gnu_only.run(GrepArgs(pattern="hello"))

        assert result.match_count == 1
        assert "hello" in result.matches
        assert "test.py" in result.matches

    @pytest.mark.asyncio
    async def test_finds_multiple_matches(self, grep_gnu_only, tmp_path):
        (tmp_path / "test.py").write_text("foo\nbar\nfoo\nbaz\nfoo\n")

        result = await grep_gnu_only.run(GrepArgs(pattern="foo"))

        assert result.match_count == 3
        assert result.matches.count("foo") == 3

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_matches(self, grep_gnu_only, tmp_path):
        (tmp_path / "test.py").write_text("def hello():\n    pass\n")

        result = await grep_gnu_only.run(GrepArgs(pattern="nonexistent"))

        assert result.match_count == 0
        assert result.matches == ""

    @pytest.mark.asyncio
    async def test_case_insensitive_for_lowercase_pattern(
        self, grep_gnu_only, tmp_path
    ):
        (tmp_path / "test.py").write_text("Hello\nHELLO\nhello\n")

        result = await grep_gnu_only.run(GrepArgs(pattern="hello"))

        assert result.match_count == 3

    @pytest.mark.asyncio
    async def test_case_sensitive_for_mixed_case_pattern(self, grep_gnu_only, tmp_path):
        (tmp_path / "test.py").write_text("Hello\nHELLO\nhello\n")

        result = await grep_gnu_only.run(GrepArgs(pattern="Hello"))

        assert result.match_count == 1

    @pytest.mark.asyncio
    async def test_respects_exclude_patterns(self, grep_gnu_only, tmp_path):
        (tmp_path / "included.py").write_text("match\n")
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "excluded.js").write_text("match\n")

        result = await grep_gnu_only.run(GrepArgs(pattern="match"))

        assert "included.py" in result.matches
        assert "excluded.js" not in result.matches

    @pytest.mark.asyncio
    async def test_searches_in_specific_path(self, grep_gnu_only, tmp_path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "test.py").write_text("match here\n")
        (tmp_path / "other.py").write_text("match here too\n")

        result = await grep_gnu_only.run(GrepArgs(pattern="match", path="subdir"))

        assert result.match_count == 1
        assert "other.py" not in result.matches

    @pytest.mark.asyncio
    async def test_respects_vibeignore_file(self, grep_gnu_only, tmp_path):
        (tmp_path / ".vibeignore").write_text("custom_dir/\n*.tmp\n")
        custom_dir = tmp_path / "custom_dir"
        custom_dir.mkdir()
        (custom_dir / "excluded.py").write_text("match\n")
        (tmp_path / "excluded.tmp").write_text("match\n")
        (tmp_path / "included.py").write_text("match\n")

        result = await grep_gnu_only.run(GrepArgs(pattern="match"))

        assert "included.py" in result.matches
        assert "excluded.py" not in result.matches
        assert "excluded.tmp" not in result.matches

    @pytest.mark.asyncio
    async def test_truncates_to_max_matches(self, grep_gnu_only, tmp_path):
        (tmp_path / "test.py").write_text("\n".join(f"line {i}" for i in range(200)))

        result = await grep_gnu_only.run(GrepArgs(pattern="line", max_matches=50))

        assert result.match_count == 50
        assert result.was_truncated


@pytest.mark.skipif(not shutil.which("rg"), reason="ripgrep not available")
class TestRipgrepBackend:
    @pytest.mark.asyncio
    async def test_smart_case_lowercase_pattern(self, grep, tmp_path):
        (tmp_path / "test.py").write_text("Hello\nHELLO\nhello\n")

        result = await grep.run(GrepArgs(pattern="hello"))

        assert result.match_count == 3

    @pytest.mark.asyncio
    async def test_smart_case_mixed_case_pattern(self, grep, tmp_path):
        (tmp_path / "test.py").write_text("Hello\nHELLO\nhello\n")

        result = await grep.run(GrepArgs(pattern="Hello"))

        assert result.match_count == 1

    @pytest.mark.asyncio
    async def test_searches_ignored_files_when_use_default_ignore_false(
        self, grep, tmp_path
    ):
        (tmp_path / ".ignore").write_text("ignored_by_rg/\n")

        ignored_dir = tmp_path / "ignored_by_rg"
        ignored_dir.mkdir()
        (ignored_dir / "file.py").write_text("match\n")
        (tmp_path / "included.py").write_text("match\n")

        result_with_ignore = await grep.run(GrepArgs(pattern="match"))
        assert "included.py" in result_with_ignore.matches
        assert "ignored_by_rg" not in result_with_ignore.matches

        result_without_ignore = await grep.run(
            GrepArgs(pattern="match", use_default_ignore=False)
        )
        assert "included.py" in result_without_ignore.matches
        assert "ignored_by_rg/file.py" in result_without_ignore.matches
