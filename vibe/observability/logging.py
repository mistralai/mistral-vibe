from __future__ import annotations

from datetime import UTC, datetime
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re

logger = logging.getLogger("vibe")


class StructuredLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        ppid = os.getppid()
        pid = os.getpid()
        level = record.levelname
        message = encode_log_message(record.getMessage())

        line = f"{timestamp} {ppid} {pid} {level} {message}"
        if record.exc_info:
            exc_text = encode_log_message(self.formatException(record.exc_info))
            line = f"{line} {exc_text}"
        return line


class _VibeFileHandler(RotatingFileHandler):
    pass


def init_file_logging(
    log_file: Path, *, target_logger: logging.Logger = logger
) -> None:
    resolved_log_file = log_file.expanduser().resolve()
    for handler in target_logger.handlers:
        if (
            isinstance(handler, _VibeFileHandler)
            and Path(handler.baseFilename) == resolved_log_file
        ):
            return

    resolved_log_file.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = int(os.environ.get("LOG_MAX_BYTES", 10 * 1024 * 1024))

    if os.environ.get("DEBUG_MODE") == "true":
        log_level_name = "DEBUG"
    else:
        log_level_name = os.environ.get("LOG_LEVEL", "WARNING").upper()
        if log_level_name not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level_name = "WARNING"

    handler = _VibeFileHandler(
        resolved_log_file, maxBytes=max_bytes, backupCount=0, encoding="utf-8"
    )
    handler.setFormatter(StructuredLogFormatter())
    handler.setLevel(getattr(logging, log_level_name, logging.WARNING))
    target_logger.setLevel(logging.DEBUG)
    target_logger.addHandler(handler)


def encode_log_message(message: str) -> str:
    return message.replace("\\", "\\\\").replace("\n", "\\n")


def decode_log_message(encoded: str) -> str:
    return re.sub(
        r"\\(.)",
        lambda match: "\n" if match.group(1) == "n" else match.group(1),
        encoded,
    )


__all__ = [
    "StructuredLogFormatter",
    "decode_log_message",
    "encode_log_message",
    "init_file_logging",
    "logger",
]
