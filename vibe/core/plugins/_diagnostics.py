from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from vibe.core.plugins._canonical import NormalizedStr
from vibe.core.plugins._snapshot import PluginSourceFormat

_ABSOLUTE_PATH = re.compile(r"^(?:[/\\]|[A-Za-z]:[/\\])")

PluginComponent = Literal[
    "manifest",
    "skill",
    "hook",
    "knowledge",
    "agent",
    "library",
    "connector",
    "mcp_server",
    "tool",
    "package",
]


class PluginDiagnosticCode(StrEnum):
    MANIFEST_INVALID = "plugin.manifest.invalid"
    SCHEMA_VERSION_UNSUPPORTED = "plugin.schema.version_unsupported"
    FORMAT_AMBIGUOUS = "plugin.compatibility.format_ambiguous"
    FORMAT_UNRECOGNIZED = "plugin.compatibility.format_unrecognized"
    EXECUTABLE_FORMAT_UNSUPPORTED = "plugin.compatibility.executable_format_unsupported"
    NAMESPACE_RESERVED = "plugin.namespace.reserved"
    NAMESPACE_COLLISION = "plugin.namespace.collision"
    NAME_COLLISION = "plugin.name.collision"
    PATH_OUTSIDE_ROOT = "plugin.path.outside_root"
    ENCODING_INVALID = "plugin.encoding.invalid"
    FILESYSTEM_ERROR = "plugin.filesystem.error"
    SKILL_INVALID = "plugin.skill.invalid"
    HOOK_INVALID = "plugin.hook.invalid"
    KNOWLEDGE_INVALID = "plugin.knowledge.invalid"
    AGENT_INVALID = "plugin.agent.invalid"
    LIBRARY_ALIAS_COLLISION = "plugin.library.alias_collision"
    CONNECTOR_RUNTIME_UNAVAILABLE = "plugin.connector.runtime_unavailable"
    CONNECTOR_UNAVAILABLE = "plugin.connector.unavailable"
    CONNECTOR_TOOL_UNAVAILABLE = "plugin.connector.tool_unavailable"
    CONNECTOR_TOOL_SCHEMA_INVALID = "plugin.connector.tool_schema_invalid"
    MCP_AUTHORIZATION_REQUIRED = "plugin.mcp.authorization_required"
    MCP_CONNECTION_FAILED = "plugin.mcp.connection_failed"
    MCP_TOOL_SCHEMA_INVALID = "plugin.mcp.tool_schema_invalid"
    TOOL_NAME_COLLISION = "plugin.tool.name_collision"
    TOOL_OVERRIDE_UNUSED = "plugin.tool_override.unused"

    @property
    def fatal(self) -> bool:
        return self in _FATAL_CODES


_FATAL_CODES = frozenset({
    PluginDiagnosticCode.MANIFEST_INVALID,
    PluginDiagnosticCode.SCHEMA_VERSION_UNSUPPORTED,
    PluginDiagnosticCode.FORMAT_AMBIGUOUS,
    PluginDiagnosticCode.FORMAT_UNRECOGNIZED,
    PluginDiagnosticCode.EXECUTABLE_FORMAT_UNSUPPORTED,
    PluginDiagnosticCode.NAMESPACE_RESERVED,
    PluginDiagnosticCode.NAMESPACE_COLLISION,
    PluginDiagnosticCode.NAME_COLLISION,
    PluginDiagnosticCode.PATH_OUTSIDE_ROOT,
    PluginDiagnosticCode.ENCODING_INVALID,
    PluginDiagnosticCode.FILESYSTEM_ERROR,
})


class PluginDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: PluginDiagnosticCode
    path: NormalizedStr
    message: NormalizedStr
    severity: Literal["info", "warning", "error"]
    plugin_name: NormalizedStr | None = None
    source_format: PluginSourceFormat | None = None
    component: PluginComponent | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_from_code(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        fields = {name: value for name, value in data.items() if name != "fatal"}
        if fields.get("severity") is not None:
            return fields
        code = PluginDiagnosticCode(fields["code"])
        return fields | {"severity": "error" if code.fatal else "warning"}

    @computed_field
    @property
    def fatal(self) -> bool:
        return self.code.fatal

    @model_validator(mode="after")
    def _reject_absolute_path(self) -> Self:
        if _ABSOLUTE_PATH.match(self.path):
            raise ValueError("diagnostic path must be relative to the plugin root")
        return self


def sort_diagnostics(
    diagnostics: Iterable[PluginDiagnostic],
) -> tuple[PluginDiagnostic, ...]:
    return tuple(
        sorted(
            diagnostics,
            key=lambda item: (
                item.plugin_name or "",
                item.path,
                item.code.value,
                item.message,
            ),
        )
    )


def surviving_plugin_names(
    candidates: Iterable[str], diagnostics: Sequence[PluginDiagnostic]
) -> tuple[str, ...]:
    dropped = {
        diagnostic.plugin_name
        for diagnostic in diagnostics
        if diagnostic.fatal and diagnostic.plugin_name is not None
    }
    return tuple(sorted(name for name in candidates if name not in dropped))
