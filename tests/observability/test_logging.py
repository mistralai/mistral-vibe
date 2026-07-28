from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from textwrap import dedent

import pytest

from vibe.observability.logging import (
    StructuredLogFormatter,
    decode_log_message,
    encode_log_message,
    init_file_logging,
)


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    return tmp_path / "vibe.log"


class TestStructuredFormatter:
    def test_format_contains_required_fields(self) -> None:
        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        parts = output.split(" ", 4)
        assert len(parts) == 5
        assert "T" in parts[0]
        assert parts[1].isdigit()
        assert parts[2].isdigit()
        assert parts[3] == "INFO"
        assert parts[4] == "Test message"

    def test_format_includes_exception(self) -> None:
        formatter = StructuredLogFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)

        assert "Error occurred" in output
        assert "ValueError" in output
        assert "test error" in output

    def test_format_escapes_newlines_in_message(self) -> None:
        formatter = StructuredLogFormatter()
        multiline_msg = dedent("""
            Line one
            Line two
            Line three""")
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg=multiline_msg,
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        assert "\n" not in output
        assert "Line one\\nLine two\\nLine three" in output

    def test_format_escapes_newlines_in_exception(self) -> None:
        formatter = StructuredLogFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)

        assert "\n" not in output
        assert "ValueError" in output
        assert "test error" in output
        assert "\\n" in output

    def test_format_output_is_single_line(self) -> None:
        formatter = StructuredLogFormatter()
        try:
            error_msg = dedent("""
                multi
                line
                error""")
            raise RuntimeError(error_msg)
        except RuntimeError:
            import sys

            exc_info = sys.exc_info()

        multiline_msg = dedent("""
            Something
            went
            wrong""")
        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg=multiline_msg,
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)
        lines = output.split("\n")

        assert len(lines) == 1


class TestInitFileLogging:
    def test_adds_handler_to_logger(
        self, log_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        test_logger = logging.getLogger("test_apply_logging")
        initial_handler_count = len(test_logger.handlers)

        init_file_logging(log_file, target_logger=test_logger)

        assert len(test_logger.handlers) == initial_handler_count + 1

    def test_creates_log_file(
        self, log_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        test_logger = logging.getLogger("test_log_file")
        test_logger.setLevel(logging.DEBUG)

        init_file_logging(log_file, target_logger=test_logger)
        test_logger.info("Test log entry")

        assert log_file.exists()

    def test_log_entry_format(
        self, log_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        test_logger = logging.getLogger("test_format")
        test_logger.setLevel(logging.DEBUG)

        init_file_logging(log_file, target_logger=test_logger)
        test_logger.warning("Test warning message")

        content = log_file.read_text()

        assert "WARNING" in content
        assert "Test warning message" in content

    def test_respects_log_level(
        self, log_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        test_logger = logging.getLogger("test_level_filter")
        test_logger.setLevel(logging.DEBUG)

        init_file_logging(log_file, target_logger=test_logger)
        test_logger.debug("Debug message")
        test_logger.info("Info message")
        test_logger.warning("Warning message")

        content = log_file.read_text()

        assert "Debug message" not in content
        assert "Info message" not in content
        assert "Warning message" in content

    def test_creates_log_directory_if_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_dir = tmp_path / "nested" / "logs"
        test_logger = logging.getLogger("test_mkdir")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        init_file_logging(log_dir / "vibe.log", target_logger=test_logger)

        assert log_dir.exists()

    def test_debug_mode_overrides_log_level(
        self, log_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        monkeypatch.setenv("DEBUG_MODE", "true")
        test_logger = logging.getLogger("test_debug_mode")
        test_logger.setLevel(logging.DEBUG)

        init_file_logging(log_file, target_logger=test_logger)
        test_logger.debug("Debug message")

        content = log_file.read_text()

        assert "Debug message" in content

    def test_invalid_log_level_defaults_to_warning(
        self, log_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "INVALID")
        test_logger = logging.getLogger("test_invalid_level")
        test_logger.setLevel(logging.DEBUG)

        init_file_logging(log_file, target_logger=test_logger)
        test_logger.info("Info message")
        test_logger.warning("Warning message")

        content = log_file.read_text()

        assert "Info message" not in content
        assert "Warning message" in content

    def test_log_max_bytes_from_env(
        self, log_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_MAX_BYTES", "5242880")  # 5 MB
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        test_logger = logging.getLogger("test_max_bytes")

        init_file_logging(log_file, target_logger=test_logger)

        # Verify handler was added with correct maxBytes
        handler = test_logger.handlers[-1]
        assert isinstance(handler, RotatingFileHandler)
        assert handler.maxBytes == 5242880

    def test_repeated_initialization_does_not_duplicate_handler(
        self, log_file: Path
    ) -> None:
        test_logger = logging.getLogger("test_idempotent_logging")
        initial_handler_count = len(test_logger.handlers)

        init_file_logging(log_file, target_logger=test_logger)
        init_file_logging(log_file, target_logger=test_logger)

        assert len(test_logger.handlers) == initial_handler_count + 1


class TestDecodeLogMessage:
    def test_plain_message_unchanged(self) -> None:
        assert decode_log_message("Hello world") == "Hello world"

    def test_decodes_escaped_newline(self) -> None:
        assert decode_log_message("hello\\nworld") == "hello\nworld"

    def test_decodes_escaped_backslash(self) -> None:
        assert decode_log_message("C:\\\\path") == "C:\\path"

    def test_decodes_escaped_backslash_before_n(self) -> None:
        # This is the bug case: C:\new encoded as C:\\new must decode back to C:\new
        assert decode_log_message("C:\\\\new") == "C:\\new"

    def test_roundtrip_with_newlines(self) -> None:
        original = "line one\nline two\nline three"
        encoded = encode_log_message(original)
        assert decode_log_message(encoded) == original

    def test_roundtrip_with_backslashes(self) -> None:
        original = "C:\\Users\\test\\file.txt"
        encoded = encode_log_message(original)
        assert decode_log_message(encoded) == original

    def test_roundtrip_with_backslash_n(self) -> None:
        original = "C:\\new folder\\notes.txt"
        encoded = encode_log_message(original)
        assert decode_log_message(encoded) == original

    def test_roundtrip_mixed(self) -> None:
        original = "path: C:\\new\nand a newline"
        encoded = encode_log_message(original)
        assert decode_log_message(encoded) == original

    def test_exception_encoding_escapes_backslashes(self) -> None:
        formatter = StructuredLogFormatter()
        try:
            raise ValueError("error in C:\\new\\path")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="fail",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)

        assert "\n" not in output
        # The backslashes in the exception should be escaped
        assert "C:\\\\new\\\\path" in output
