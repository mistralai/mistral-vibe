from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field
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
    literal: bool = Field(default=False, exclude=True)
    """Match this scope as text, never as a glob.

    A scope recorded because nothing wider was honest has to stay out of reach of
    the wider rules: ``cat *`` is a fair grant for ``cat $FILE`` and would
    otherwise cover ``cat notes > /etc/cron.d/pwn``, whose whole point is that
    only the command as written may be granted. The command's own text can carry
    glob characters too, so ``git log *`` must not read as a pattern either.

    Excluded from serialization: only ``covers`` reads it, and every caller
    resolves the permission afresh from the call. Putting it on the wire would
    add a field to the ACP and Runtime payloads that nothing downstream reads.
    """
