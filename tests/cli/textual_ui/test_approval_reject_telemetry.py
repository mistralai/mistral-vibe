from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server.session import AppServerSession
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp


@pytest.mark.asyncio
async def test_every_deny_records_reject_approval() -> None:
    app = build_test_vibe_app()
    telemetry = MagicMock()
    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.telemetry = telemetry
    app._app_server = app_server

    with patch.object(app, "_respond_to_approval", AsyncMock()):
        await app.on_approval_app_approval_rejected(
            ApprovalApp.ApprovalRejected(tool_name="bash", tool_args={})
        )

    telemetry.record.assert_called_once_with(
        "vibe.user_cancelled_action", {"action": "reject_approval"}
    )
