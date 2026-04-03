from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest

from vibe.core.logger import StructuredLogFormatter, apply_logging_config


@pytest.fixture
def mock_log_dir(tmp_path: Path):
    """Mock LOG_DIR and LOG_FILE to use tmp_path for testing."""
    mock_dir = MagicMock()
    mock_dir.path = tmp_path
    mock_file = MagicMock()
    mock_file.path = tmp_path / "vibe.log"
    with (
        patch("vibe.core.logger.LOG_DIR", mock_dir),
        patch("vibe.core.logger.LOG_FILE", mock_file),
    ):
        yield tmp_path


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


class TestApplyLoggingConfig:
    def test_adds_handler_to_logger(
        self, mock_log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        test_logger = logging.getLogger("test_apply_logging")
        initial_handler_count = len(test_logger.handlers)

        apply_logging_config(test_logger)

        assert len(test_logger.handlers) == initial_handler_count + 1

    def test_creates_log_file(
        self, mock_log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        test_logger = logging.getLogger("test_log_file")
        test_logger.setLevel(logging.DEBUG)

        apply_logging_config(test_logger)
        test_logger.info("Test log entry")

        log_file = mock_log_dir / "vibe.log"
        assert log_file.exists()

    def test_log_entry_format(
        self, mock_log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        test_logger = logging.getLogger("test_format")
        test_logger.setLevel(logging.DEBUG)

        apply_logging_config(test_logger)
        test_logger.warning("Test warning message")

        log_file = mock_log_dir / "vibe.log"
        content = log_file.read_text()

        assert "WARNING" in content
        assert "Test warning message" in content

    def test_respects_log_level(
        self, mock_log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        test_logger = logging.getLogger("test_level_filter")
        test_logger.setLevel(logging.DEBUG)

        apply_logging_config(test_logger)
        test_logger.debug("Debug message")
        test_logger.info("Info message")
        test_logger.warning("Warning message")

        log_file = mock_log_dir / "vibe.log"
        content = log_file.read_text()

        assert "Debug message" not in content
        assert "Info message" not in content
        assert "Warning message" in content

    def test_creates_log_directory_if_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_dir = tmp_path / "nested" / "logs"
        mock_dir = MagicMock()
        mock_dir.path = log_dir
        mock_file = MagicMock()
        mock_file.path = log_dir / "vibe.log"
        with (
            patch("vibe.core.logger.LOG_DIR", mock_dir),
            patch("vibe.core.logger.LOG_FILE", mock_file),
        ):
            monkeypatch.setenv("LOG_LEVEL", "DEBUG")
            test_logger = logging.getLogger("test_mkdir")

            apply_logging_config(test_logger)

            assert log_dir.exists()

    def test_debug_mode_overrides_log_level(
        self, mock_log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        monkeypatch.setenv("DEBUG_MODE", "true")
        test_logger = logging.getLogger("test_debug_mode")
        test_logger.setLevel(logging.DEBUG)

        apply_logging_config(test_logger)
        test_logger.debug("Debug message")

        log_file = mock_log_dir / "vibe.log"
        content = log_file.read_text()

        assert "Debug message" in content

    def test_invalid_log_level_defaults_to_warning(
        self, mock_log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "INVALID")
        test_logger = logging.getLogger("test_invalid_level")
        test_logger.setLevel(logging.DEBUG)

        apply_logging_config(test_logger)
        test_logger.info("Info message")
        test_logger.warning("Warning message")

        log_file = mock_log_dir / "vibe.log"
        content = log_file.read_text()

        assert "Info message" not in content
        assert "Warning message" in content

    def test_log_max_bytes_from_env(
        self, mock_log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_MAX_BYTES", "5242880")  # 5 MB
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        test_logger = logging.getLogger("test_max_bytes")

        apply_logging_config(test_logger)

        # Verify handler was added with correct maxBytes
        handler = test_logger.handlers[-1]
        assert isinstance(handler, RotatingFileHandler)
        assert handler.maxBytes == 5242880
