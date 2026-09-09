from __future__ import annotations

from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.cli.textual_ui._resume_errors import resume_failure_message


def _store_newer_error() -> AppServerResponseError:
    return AppServerResponseError(
        ProtocolError(
            code=ProtocolErrorCode.INTERNAL_ERROR,
            message="This session was written by a newer store format (1.3) "
            "and needs a newer reader to open.",
            data={"harnessCode": "store_requires_newer_reader"},
        )
    )


def test_resume_message_tells_user_to_upgrade_for_a_newer_store() -> None:
    """Prepare: A resume error carrying the newer-store harness code.
    Do: Build the transcript message.
    Assert: The user is told to update Vibe with the flag, not shown store internals.
    """
    # Prepare
    error = _store_newer_error()

    # Do
    message = resume_failure_message(error, "Failed to resume session")

    # Assert
    assert "newer version of Vibe" in message
    assert "--check-upgrade" in message
    assert "store format" not in message
    assert "Failed to resume session" not in message


def test_resume_message_passes_other_errors_through_with_prefix() -> None:
    """Prepare: An unrelated resume failure.
    Do: Build the transcript message.
    Assert: The ordinary prefix and underlying error are shown verbatim.
    """
    # Prepare
    error = AppServerResponseError(
        ProtocolError(
            code=ProtocolErrorCode.NOT_FOUND,
            message="Session not found: abc",
            data=None,
        )
    )

    # Do
    message = resume_failure_message(error, "Failed to load session")

    # Assert
    assert message == "Failed to load session: Session not found: abc"


def test_resume_message_handles_non_app_server_errors() -> None:
    """Prepare: A plain exception with no protocol data.
    Do: Build the transcript message.
    Assert: It falls back to the prefixed error text.
    """
    # Prepare
    error = RuntimeError("boom")

    # Do
    message = resume_failure_message(error, "Failed to resume session")

    # Assert
    assert message == "Failed to resume session: boom"
