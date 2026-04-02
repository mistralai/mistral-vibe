from __future__ import annotations


class ServiceTeleportError(Exception):
    """Base exception for teleport errors."""


class ServiceTeleportNotSupportedError(ServiceTeleportError):
    """Raised when teleport is not supported in current environment."""
