from __future__ import annotations

import logging
import os
from pathlib import Path
import stat

import pytest

from vibe.acp import acp_logger


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_acp_logger_creates_owner_only_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACP transcripts hold full session messages, so they land owner-only."""
    log_dir = tmp_path / "acp"
    monkeypatch.setattr(acp_logger, "ACP_LOG_DIR", log_dir)
    monkeypatch.setattr(acp_logger, "ACP_LOG_FILE", log_dir / "messages.jsonl")
    monkeypatch.setattr(acp_logger, "_logger", None)

    try:
        acp_logger._get_logger().info({"msg": "hello"})

        assert stat.S_IMODE(log_dir.stat().st_mode) & 0o077 == 0
        messages_file = log_dir / "messages.jsonl"
        assert stat.S_IMODE(messages_file.stat().st_mode) & 0o077 == 0
        assert "hello" in messages_file.read_text(encoding="utf-8")
    finally:
        logging.getLogger("acp_messages").handlers.clear()
