from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.utils import get_server_url_from_api_base
from vibe.core.utils.io import read_safe


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
        assert read_safe(f) == "café\n"

    def test_falls_back_on_non_utf8(self, tmp_path: Path) -> None:
        f = tmp_path / "latin.txt"
        # \x81 invalid UTF-8 and undefined in CP1252 → U+FFFD on all platforms
        f.write_bytes(b"maf\x81\n")
        result = read_safe(f)
        assert result == "maf�\n"

    def test_raise_on_error_true_utf8_succeeds(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("café\n", encoding="utf-8")
        assert read_safe(f, raise_on_error=True) == "café\n"

    def test_raise_on_error_true_non_utf8_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.txt"
        # Invalid UTF-8; with raise_on_error=True we use default encoding (strict), so decode errors propagate
        f.write_bytes(b"maf\x81\n")
        assert read_safe(f, raise_on_error=False) == "maf�\n"
        with pytest.raises(UnicodeDecodeError):
            read_safe(f, raise_on_error=True)

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert read_safe(f) == ""

    def test_binary_garbage_does_not_raise(self, tmp_path: Path) -> None:
        f = tmp_path / "garbage.bin"
        f.write_bytes(bytes(range(256)))
        result = read_safe(f)
        assert isinstance(result, str)

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_safe(tmp_path / "nope.txt")
