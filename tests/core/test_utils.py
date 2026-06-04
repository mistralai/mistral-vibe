from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vibe.core.utils import compact_complete_display, get_server_url_from_api_base
import vibe.core.utils.io as io_utils
from vibe.core.utils.io import (
    _FILE_WRITE_LOCKS,
    decode_safe,
    file_write_lock,
    read_lines_safe,
    read_lines_safe_async,
    read_safe,
    read_safe_async,
)


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        ("https://api.mistral.ai/v1", "https://api.mistral.ai"),
        ("https://on-prem.example.com/v1", "https://on-prem.example.com"),
        ("http://localhost:8080/v2", "http://localhost:8080"),
        ("not-a-url", None),
        ("ftp://example.com/v1", None),
    ],
)
def test_get_server_url_from_api_base(api_base, expected):
    assert get_server_url_from_api_base(api_base) == expected


class TestCompactCompleteDisplay:
    def test_includes_session_ids_when_available(self) -> None:
        assert compact_complete_display(
            old_session_id="11111111-1111-1111-1111-111111111111",
            new_session_id="22222222-2222-2222-2222-222222222222",
        ) == (
            "Compaction completed.\n"
            "session: 11111111 (before compaction) → 22222222 (after compaction)"
        )

    def test_returns_base_message_without_session_ids(self) -> None:
        assert compact_complete_display() == "Compaction completed."


class TestReadSafe:
    def test_reads_utf8(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("café\n", encoding="utf-8")
        assert read_safe(f).text == "café\n"
        assert decode_safe(f.read_bytes()).text == "café\n"

    def test_falls_back_on_non_utf8(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "latin.txt"
        # \x81 invalid UTF-8 and undefined in CP1252 → U+FFFD on all platforms
        f.write_bytes(b"maf\x81\n")
        monkeypatch.setattr(io_utils, "_encoding_from_best_match", lambda _raw: None)
        result = read_safe(f)
        assert result.text == "maf�\n"

    def test_falls_back_to_detected_encoding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "utf16.txt"
        expected = "hello été\n"
        f.write_bytes(expected.encode("utf-16le"))
        monkeypatch.setattr(
            io_utils.locale, "getpreferredencoding", lambda _do_setlocale: "utf-8"
        )

        assert read_safe(f).text == expected

    def test_raise_on_error_true_utf8_succeeds(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("café\n", encoding="utf-8")
        assert read_safe(f, raise_on_error=True).text == "café\n"

    def test_raise_on_error_true_non_utf8_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "bad.txt"
        # Invalid UTF-8; with raise_on_error=True we use default encoding (strict), so decode errors propagate
        f.write_bytes(b"maf\x81\n")
        monkeypatch.setattr(io_utils, "_encoding_from_best_match", lambda _raw: None)
        assert read_safe(f, raise_on_error=False).text == "maf�\n"
        with pytest.raises(UnicodeDecodeError):
            read_safe(f, raise_on_error=True)

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert read_safe(f).text == ""

    def test_binary_garbage_does_not_raise(self, tmp_path: Path) -> None:
        f = tmp_path / "garbage.bin"
        f.write_bytes(bytes(range(256)))
        result = read_safe(f)
        assert isinstance(result.text, str)

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_safe(tmp_path / "nope.txt")


class TestReadSafeNewlines:
    def test_lf(self, tmp_path: Path) -> None:
        f = tmp_path / "lf.txt"
        f.write_bytes(b"a\nb\nc\n")
        got = read_safe(f)
        assert got.text == "a\nb\nc\n"
        assert got.newline == "\n"

    def test_crlf(self, tmp_path: Path) -> None:
        f = tmp_path / "crlf.txt"
        f.write_bytes(b"a\r\nb\r\nc\r\n")
        got = read_safe(f)
        assert got.text == "a\nb\nc\n"
        assert got.newline == "\r\n"

    def test_cr(self, tmp_path: Path) -> None:
        f = tmp_path / "cr.txt"
        f.write_bytes(b"a\rb\rc\r")
        got = read_safe(f)
        assert got.text == "a\nb\nc\n"
        assert got.newline == "\r"

    def test_mixed_picks_most_frequent(self, tmp_path: Path) -> None:
        f = tmp_path / "mixed.txt"
        f.write_bytes(b"a\r\nb\r\nc\rd\n")
        got = read_safe(f)
        assert got.text == "a\nb\nc\nd\n"
        assert got.newline == "\r\n"

    @pytest.mark.parametrize(("linesep", "expected"), [("\n", "\n"), ("\r\n", "\r\n")])
    def test_no_newline_defaults_to_os_linesep(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        linesep: str,
        expected: str,
    ) -> None:
        monkeypatch.setattr(io_utils.os, "linesep", linesep)
        f = tmp_path / "single.txt"
        f.write_bytes(b"hello")
        got = read_safe(f)
        assert got.text == "hello"
        assert got.newline == expected

    @pytest.mark.parametrize(("linesep", "expected"), [("\n", "\n"), ("\r\n", "\r\n")])
    def test_empty_defaults_to_os_linesep(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        linesep: str,
        expected: str,
    ) -> None:
        monkeypatch.setattr(io_utils.os, "linesep", linesep)
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        got = read_safe(f)
        assert got.text == ""
        assert got.newline == expected

    def test_decode_safe_reports_newline(self) -> None:
        got = decode_safe(b"a\r\nb\r\n")
        assert got.text == "a\nb\n"
        assert got.newline == "\r\n"

    @pytest.mark.asyncio
    async def test_async_reports_newline(self, tmp_path: Path) -> None:
        f = tmp_path / "crlf.txt"
        f.write_bytes(b"a\r\nb\r\n")
        got = await read_safe_async(f)
        assert got.text == "a\nb\n"
        assert got.newline == "\r\n"


class TestReadSafeResultEncoding:
    def test_reports_utf8_for_plain_utf8_file(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("ok\n", encoding="utf-8")
        got = read_safe(f)
        assert got.text == "ok\n"
        assert got.encoding == "utf-8"

    @pytest.mark.asyncio
    async def test_async_reports_utf16_when_bom_present(self, tmp_path: Path) -> None:
        f = tmp_path / "u16.txt"
        f.write_bytes("a\n".encode("utf-16"))
        got = await read_safe_async(f)
        assert got.encoding == "utf-16-le"
        # utf-16-le leaves the BOM as U+FEFF in the string (unlike utf-8-sig).
        assert got.text == "\ufeffa\n"


class TestReadSafeAsync:
    @pytest.mark.asyncio
    async def test_raise_on_error_final_utf8_strict_or_replace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """raise_on_error controls strict vs replace on the last UTF-8 fallback."""
        f = tmp_path / "bad.txt"
        f.write_bytes(b"maf\x81\n")
        monkeypatch.setattr(io_utils, "_encoding_from_best_match", lambda _raw: None)
        assert (await read_safe_async(f, raise_on_error=False)).text == "maf�\n"
        with pytest.raises(UnicodeDecodeError):
            await read_safe_async(f, raise_on_error=True)


class TestReadLinesSafe:
    def test_small_file_fully_read(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\n", encoding="utf-8")
        got = read_lines_safe(f, limit=100, max_bytes=1024)
        assert got.lines == ["a", "b", "c"]
        assert got.total_lines == 3
        assert got.was_truncated is False

    def test_no_trailing_newline(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("a\nb", encoding="utf-8")
        got = read_lines_safe(f, limit=100, max_bytes=1024)
        assert got.lines == ["a", "b"]
        assert got.total_lines == 2
        assert got.was_truncated is False

    def test_truncates_at_limit(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("".join(f"line {i}\n" for i in range(1, 101)), encoding="utf-8")
        got = read_lines_safe(f, limit=10, max_bytes=1024)
        assert got.lines == [f"line {i}" for i in range(1, 11)]
        assert got.total_lines is None
        assert got.was_truncated is True

    def test_offset_skips_leading_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("".join(f"line {i}\n" for i in range(1, 11)), encoding="utf-8")
        got = read_lines_safe(f, start_line=3, limit=2, max_bytes=1024)
        assert got.lines == ["line 3", "line 4"]
        assert got.was_truncated is True

    def test_offset_past_eof_reports_total(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("a\nb\n", encoding="utf-8")
        got = read_lines_safe(f, start_line=100, limit=10, max_bytes=1024)
        assert got.lines == []
        assert got.total_lines == 2
        assert got.was_truncated is False

    def test_does_not_load_whole_file(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("".join(f"line {i}\n" for i in range(1_000_000)), encoding="utf-8")
        got = read_lines_safe(f, limit=5, max_bytes=1024)
        assert got.lines == [f"line {i}" for i in range(5)]
        assert got.total_lines is None
        assert got.was_truncated is True

    def test_oversized_single_line_returns_partial(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x" * 5000 + "\n", encoding="utf-8")
        got = read_lines_safe(f, limit=10, max_bytes=1024)
        assert got.lines == ["x" * 1024]
        assert got.was_truncated is True

    def test_cumulative_byte_budget_truncates(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("".join("x" * 200 + "\n" for _ in range(50)), encoding="utf-8")
        got = read_lines_safe(f, limit=50, max_bytes=1024)
        assert 0 < len(got.lines) < 50
        assert got.total_lines is None
        assert got.was_truncated is True

    def test_oversized_unselected_line_is_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x" * 5000 + "\nkept\n", encoding="utf-8")
        got = read_lines_safe(f, start_line=2, limit=10, max_bytes=1024)
        assert got.lines == ["kept"]

    @pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-16"])
    def test_utf16_is_decoded(self, tmp_path: Path, encoding: str) -> None:
        f = tmp_path / "u16.txt"
        f.write_bytes("héllo\nwörld\n".encode(encoding))
        got = read_lines_safe(f, limit=10, max_bytes=4096)
        assert got.lines[-1] == "wörld"
        # A leading BOM may remain as U+FEFF on the first line.
        assert got.lines[0].endswith("héllo")

    @pytest.mark.asyncio
    async def test_async_matches_sync(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\n", encoding="utf-8")
        got = await read_lines_safe_async(f, limit=2, max_bytes=1024)
        assert got.lines == ["a", "b"]
        assert got.was_truncated is True


class TestFileWriteLock:
    @pytest.mark.asyncio
    async def test_same_lock_for_different_path_spellings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "f.txt"
        path.touch()
        _FILE_WRITE_LOCKS.clear()

        order: list[str] = []
        held = asyncio.Event()
        release = asyncio.Event()

        async def first() -> None:
            async with file_write_lock(path):
                order.append("first-acquired")
                held.set()
                await release.wait()
                order.append("first-released")

        async def second() -> None:
            await held.wait()
            # Same file, different spelling — must contend on the same lock.
            async with file_write_lock(Path("f.txt")):
                order.append("second-acquired")

        t1 = asyncio.create_task(first())
        t2 = asyncio.create_task(second())
        await held.wait()
        await asyncio.sleep(0)
        assert order == ["first-acquired"]
        release.set()
        await asyncio.gather(t1, t2)
        assert order == ["first-acquired", "first-released", "second-acquired"]
