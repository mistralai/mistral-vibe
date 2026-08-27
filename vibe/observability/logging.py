from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re

from vibe.config_values import DEFAULT_LOG_LEVEL

logger = logging.getLogger("vibe")


def log_model_call_success(
    alias: str,
    duration_ms: int,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
) -> None:
    logger.info(
        "Model call completed model=%s duration_ms=%d prompt_tokens=%d completion_tokens=%d cached_tokens=%d",
        alias,
        duration_ms,
        prompt_tokens,
        completion_tokens,
        cached_tokens,
    )


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

    handler = _VibeFileHandler(
        resolved_log_file, maxBytes=max_bytes, backupCount=0, encoding="utf-8"
    )
    handler.setFormatter(StructuredLogFormatter())
    target_logger.setLevel(logging.DEBUG)
    target_logger.addHandler(handler)
    _log_level_state._apply_effective(target_logger)


LOG_LEVELS: frozenset[str] = frozenset({
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
})


def _vibe_file_handlers(
    target_logger: logging.Logger = logger,
) -> list[_VibeFileHandler]:
    return [h for h in target_logger.handlers if isinstance(h, _VibeFileHandler)]


def _get_env_log_level() -> str | None:
    if os.environ.get("DEBUG_MODE") == "true":
        return "DEBUG"
    env_level = os.environ.get("LOG_LEVEL")
    if env_level and env_level.upper() in LOG_LEVELS:
        return env_level.upper()
    return None


@dataclass(frozen=True)
class LogLevelChain:
    session: str | None
    env: str | None
    config: str | None
    effective: str


class _LogLevelState:
    def __init__(self) -> None:
        self._session_override: str | None = None
        self._config_level: str | None = None

    @property
    def session_override(self) -> str | None:
        return self._session_override

    @property
    def config_level(self) -> str | None:
        return self._config_level

    def chain(self) -> LogLevelChain:
        env = _get_env_log_level()
        effective = (
            self._session_override or env or self._config_level or DEFAULT_LOG_LEVEL
        )
        return LogLevelChain(
            session=self._session_override,
            env=env,
            config=self._config_level,
            effective=effective,
        )

    def set_session_override(self, level: str | None) -> None:
        self._session_override = level
        self._apply_effective()

    def set_config_level(self, level: str | None) -> None:
        self._config_level = level
        self._apply_effective()

    def _apply_effective(self, target_logger: logging.Logger = logger) -> None:
        effective = self.chain().effective
        for handler in _vibe_file_handlers(target_logger):
            handler.setLevel(effective)


_log_level_state = _LogLevelState()


def set_log_level(level: str, *, target_logger: logging.Logger = logger) -> str:
    normalized = level.strip().upper()
    if normalized not in LOG_LEVELS:
        raise ValueError(
            f"Invalid log level {level!r}; expected one of {sorted(LOG_LEVELS)}"
        )
    for handler in _vibe_file_handlers(target_logger):
        handler.setLevel(normalized)
    return normalized


def get_effective_log_level(*, target_logger: logging.Logger = logger) -> str:
    handlers = _vibe_file_handlers(target_logger)
    if not handlers:
        return "WARNING"
    return logging.getLevelName(handlers[0].level)


def get_log_level_chain() -> LogLevelChain:
    return _log_level_state.chain()


def get_session_override() -> str | None:
    return _log_level_state.session_override


def set_session_override(level: str | None) -> None:
    _log_level_state.set_session_override(level)


def set_config_log_level(level: str | None) -> None:
    _log_level_state.set_config_level(level)


def encode_log_message(message: str) -> str:
    return message.replace("\\", "\\\\").replace("\n", "\\n")


def decode_log_message(encoded: str) -> str:
    return re.sub(
        r"\\(.)",
        lambda match: "\n" if match.group(1) == "n" else match.group(1),
        encoded,
    )


__all__ = [
    "LOG_LEVELS",
    "LogLevelChain",
    "StructuredLogFormatter",
    "decode_log_message",
    "encode_log_message",
    "get_effective_log_level",
    "get_log_level_chain",
    "get_session_override",
    "init_file_logging",
    "log_model_call_success",
    "logger",
    "set_config_log_level",
    "set_log_level",
    "set_session_override",
]
