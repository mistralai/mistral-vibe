from __future__ import annotations

from enum import StrEnum, auto
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from vibe.core.tools.ui import ToolUIData


class AuthStatus(StrEnum):
    OK = auto()
    NEEDS_AUTH = auto()
    STATIC = auto()
    STDIO = auto()


class _OpenArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class MCPToolResult(BaseModel):
    ok: bool = True
    server: str
    tool: str
    text: str | None = None
    structured: dict[str, Any] | None = None


class MCPTool(
    BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState],
    ToolUIData[_OpenArgs, MCPToolResult],
):
    _server_name: ClassVar[str] = ""
    _remote_name: ClassVar[str] = ""
    _is_connector: ClassVar[bool] = False

    @classmethod
    def get_server_name(cls) -> str | None:
        return cls._server_name or None

    @classmethod
    def get_remote_name(cls) -> str:
        return cls._remote_name or cls.get_name()

    @classmethod
    def is_connector(cls) -> bool:
        return cls._is_connector


class RemoteTool(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        validation_alias="inputSchema",
    )
    output_schema: dict[str, Any] | None = Field(
        default=None, validation_alias="outputSchema"
    )

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("MCP tool missing valid 'name'")
        return v

    @field_validator("input_schema", mode="before")
    @classmethod
    def _normalize_schema(cls, v: Any) -> dict[str, Any]:
        if v is None:
            return {"type": "object", "properties": {}}
        return _coerce_schema(v, "inputSchema")

    @field_validator("output_schema", mode="before")
    @classmethod
    def _normalize_output_schema(cls, v: Any) -> dict[str, Any] | None:
        if v is None:
            return None
        return _coerce_schema(v, "outputSchema")


def _coerce_schema(value: Any, field: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            value = dump()
        except Exception:
            raise ValueError(
                f"{field} must be a dict or have a valid model_dump method"
            )
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a dict")
    return value
