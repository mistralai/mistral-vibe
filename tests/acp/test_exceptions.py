from __future__ import annotations

from vibe.acp.exceptions import (
    COMPACTION_FAILED,
    IMAGES_NOT_SUPPORTED,
    CompactionError,
    ImagesNotSupportedError,
    from_app_server_error,
    from_public_error,
)
from vibe.app_server.models import PublicError
from vibe.app_server.protocol import ProtocolError, ProtocolErrorCode


def test_public_compaction_error_maps_to_acp_error() -> None:
    error = from_public_error(
        PublicError(
            code="compaction_failed",
            message="Compaction failed",
            details={"reason": "tool_call"},
        )
    )

    assert isinstance(error, CompactionError)
    assert error.code == COMPACTION_FAILED
    assert error.data == {"reason": "tool_call"}


def test_app_server_compaction_error_maps_to_acp_error() -> None:
    error = from_app_server_error(
        ProtocolError(
            code=ProtocolErrorCode.COMPACTION_FAILED,
            message="Compaction failed",
            data={"reason": "empty_summary"},
        )
    )

    assert isinstance(error, CompactionError)
    assert error.code == COMPACTION_FAILED
    assert error.data == {"reason": "empty_summary"}


def test_public_images_not_supported_maps_to_acp_error() -> None:
    error = from_public_error(
        PublicError(
            code="images_not_supported",
            message="model does not support images",
            details={"model": "text-model"},
        )
    )

    assert isinstance(error, ImagesNotSupportedError)
    assert error.code == IMAGES_NOT_SUPPORTED
