"""Smart-approve activation over the experimental Unified Harness backend.

Activation is the `--smart-approve` CLI flag, which selects the smart-approve
agent; the Runtime then gates each tool call with the risk classifier via the
`"classify"` tool mode. The host needs no smart-approve registration.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import uuid

import pytest

from vibe import _experimental_harness
from vibe.observability import logging as vibe_logging


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    _experimental_harness.add_smart_approve_argument(parser)
    return parser.parse_args(argv)


def test_smart_approve_flag_defaults_to_false() -> None:
    # Do / Assert
    assert _parse([]).smart_approve is False


def test_smart_approve_flag_parses_when_present() -> None:
    # Do / Assert
    assert _parse(["--smart-approve"]).smart_approve is True


def test_host_creation_needs_no_smart_approve_registration() -> None:
    """Smart approve is a Runtime tool-gate concern, so the host needs no hook
    registration: creation just returns a usable Unified Harness host.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    host = _experimental_harness.create_experimental_harness_host()

    assert host is not None


def _isolated_file_logging(tmp_path: Path) -> tuple[logging.Logger, Path]:
    target = logging.getLogger(f"vibe_test_{uuid.uuid4().hex}")
    target.propagate = False
    log_file = tmp_path / "vibe.log"
    vibe_logging.init_file_logging(log_file, target_logger=target)
    vibe_logging.set_log_level("DEBUG", target_logger=target)
    return target, log_file


def _teardown_file_logging(target: logging.Logger) -> None:
    harness = logging.getLogger("mistralai_vibe_local_harness")
    for source in (target, harness):
        for handler in list(source.handlers):
            if isinstance(handler, vibe_logging._VibeFileHandler):
                handler.flush()
                source.removeHandler(handler)


def test_harness_logger_is_written_to_the_vibe_log_file(tmp_path: Path) -> None:
    """*Prepare*: Initialize vibe file logging with the harness logger wired in.
    *Do*: Emit a record on the Unified Harness logger.
    *Assert*: The record lands in the vibe log file.
    """
    target, log_file = _isolated_file_logging(tmp_path)
    try:
        logging.getLogger("mistralai_vibe_local_harness.vibe._smart_approve").info(
            "smart_approve verdict=allow tool=probe"
        )
        for handler in logging.getLogger("mistralai_vibe_local_harness").handlers:
            handler.flush()

        assert "smart_approve verdict=allow tool=probe" in log_file.read_text()
    finally:
        _teardown_file_logging(target)


def test_classify_verdict_reaches_the_log_file(tmp_path: Path) -> None:
    """*Prepare*: Wire file logging and classify a call with a stub classifier.
    *Do*: Classify a safe read.
    *Assert*: The classifier's verdict line is written to the vibe log file.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        ClassificationTier,
        ClassificationVerdict,
        LocalRuntimeAdapterConfig,
        RiskClassificationRequest,
        RiskClassificationResult,
        classify_tool_call,
    )

    class _StubClassifier:
        async def classify(
            self, request: RiskClassificationRequest
        ) -> RiskClassificationResult:
            return RiskClassificationResult(
                verdict=ClassificationVerdict.ALLOW,
                tier=ClassificationTier.FAST,
                reason="read-only",
            )

    target, log_file = _isolated_file_logging(tmp_path)
    try:

        async def _classify() -> None:
            await classify_tool_call(
                RiskClassificationRequest(
                    tool_name="file_system.read_file", args={"path": "x"}
                ),
                config=LocalRuntimeAdapterConfig(),
                classifier_factory=lambda _config: _StubClassifier(),
            )

        asyncio.run(_classify())
        for file_handler in logging.getLogger("mistralai_vibe_local_harness").handlers:
            file_handler.flush()

        written = log_file.read_text()
        assert "smart_approve verdict=allow" in written
        assert "tool=file_system.read_file" in written
    finally:
        _teardown_file_logging(target)
