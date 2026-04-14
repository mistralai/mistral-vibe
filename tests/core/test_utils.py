from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.utils import get_server_url_from_api_base
import vibe.core.utils.io as io_utils
from vibe.core.utils.io import decode_safe, read_safe, read_safe_async


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
