from __future__ import annotations

from pathlib import Path
import shutil
import sys

from vibe.cli.update_notifier.adapters.fallback_update_gateway import (
    FallbackUpdateGateway,
)
from vibe.cli.update_notifier.adapters.pypi_update_gateway import PyPIUpdateGateway
from vibe.cli.update_notifier.adapters.uv_tool_update_gateway import UvToolUpdateGateway
from vibe.cli.update_notifier.ports.update_gateway import UpdateGateway

_UV_TOOL_RECEIPT = "uv-receipt.toml"


def uv_tool_receipt_path() -> Path:
    return Path(sys.prefix) / _UV_TOOL_RECEIPT


def create_update_gateway(project_name: str) -> UpdateGateway:
    pypi = PyPIUpdateGateway(project_name)
    receipt_path = uv_tool_receipt_path()
    if not receipt_path.is_file():
        return pypi
    if not (uv := shutil.which("uv")):
        return pypi
    # uv resolves updates with the user's policy (exclude-newer, indexes), which
    # can hold back releases PyPI already lists. PyPI only answers when uv cannot:
    # uv too old for --outdated, or a git/path install uv never reports on.
    return FallbackUpdateGateway(
        primary=UvToolUpdateGateway(
            project_name, receipt_path=receipt_path, uv_command=(uv,)
        ),
        fallback=pypi,
    )
