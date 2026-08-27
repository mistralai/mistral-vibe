from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

__all__ = ["PermissionScope", "RequiredPermission"]


class PermissionScope(StrEnum):
    COMMAND_PATTERN = auto()
    OUTSIDE_DIRECTORY = auto()
    FILE_PATTERN = auto()
    URL_PATTERN = auto()


class RequiredPermission(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )

    scope: PermissionScope
    invocation_pattern: str
    session_pattern: str
    label: str
