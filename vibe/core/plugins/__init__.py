from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from vibe.core.plugins._adapter import (
    PluginSourceFormatUnsupportedError,
    build_snapshot,
    plugin_identity,
)
from vibe.core.plugins._canonical import (
    NormalizedJson,
    NormalizedStr,
    PluginEncodingError,
    canonical_json,
    canonical_json_digest,
    normalize_json,
    normalize_nfc,
)
from vibe.core.plugins._compatibility import (
    DetectedPluginFormat,
    plugin_runtime_state_names,
)
from vibe.core.plugins._content import digest_plugin_tree
from vibe.core.plugins._diagnostics import (
    PluginComponent,
    PluginDiagnostic,
    PluginDiagnosticCode,
    sort_diagnostics,
    surviving_plugin_names,
)
from vibe.core.plugins._drift import (
    PluginRouteKey,
    PluginRouteStatus,
    PluginSourceRef,
    plugin_route_source,
    reconcile_plugin_routes,
    retain_pinned_tools,
    route_key,
)
from vibe.core.plugins._materialize import (
    MaterializedPluginSet,
    PluginMaterializer,
    PluginToolCatalog,
    PluginToolDefinition,
    PluginToolGroup,
    PluginToolRoute,
)
from vibe.core.plugins._naming import (
    ToolGroupIdentity,
    identifier_segment,
    plugin_mcp_group_name,
    resolve_tool_group_names,
    tool_function_name,
)
from vibe.core.plugins._native import (
    NativePluginResolver,
    PluginAgentDefinition,
    PluginConfigIssue,
    PluginConnectorDefinition,
    PluginDescriptor,
    PluginKnowledgeDefinition,
    PluginLibraryDefinition,
    PluginMCPServerDefinition,
    PluginResolver,
    PluginUnsupportedComponent,
    ResolvedPluginSet,
    plugin_skill_runtime_path,
    plugin_skill_translation,
)
from vibe.core.plugins._paths import (
    PluginPathOutsideRootError,
    PluginPathRef,
    plugin_path_ref,
    resolve_plugin_path,
)
from vibe.core.plugins._redaction import (
    PLUGIN_PLACEHOLDER,
    REDACTED,
    mcp_server_secrets,
    redact_argv,
    redact_failure,
    redact_names,
    redact_url,
    redact_values,
    sanitize_message,
)
from vibe.core.plugins._snapshot import (
    HOST_HOOK_ENVIRONMENT,
    PluginAgentSnapshot,
    PluginConnectorSnapshot,
    PluginHookSnapshot,
    PluginKnowledgeSnapshot,
    PluginLibrarySnapshot,
    PluginSkillSnapshot,
    PluginSnapshotEntry,
    PluginSnapshotIdentityError,
    PluginSourceFormat,
    PluginSourceKind,
    PluginToolExposure,
    PluginToolGroupSnapshot,
    PluginToolRouteSnapshot,
    PluginToolSnapshot,
    ResolvedPluginSnapshot,
    build_plugin_snapshot,
    hook_environment_names,
    snapshot_bytes,
    snapshot_digest,
    validate_resolved_plugin_snapshot,
)

if TYPE_CHECKING:
    from vibe.core.plugins._catalog import (
        PluginConnectorCatalog,
        PluginMCPDiscovery,
        PluginMCPDiscoveryError,
        RegistryConnectorCatalog,
        RegistryMCPDiscovery,
        build_tool_catalog,
    )

_LAZY = {
    "PluginConnectorCatalog": "_catalog",
    "PluginMCPDiscovery": "_catalog",
    "PluginMCPDiscoveryError": "_catalog",
    "RegistryConnectorCatalog": "_catalog",
    "RegistryMCPDiscovery": "_catalog",
    "build_tool_catalog": "_catalog",
}


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{module}"), name)


__all__ = [
    "HOST_HOOK_ENVIRONMENT",
    "PLUGIN_PLACEHOLDER",
    "REDACTED",
    "DetectedPluginFormat",
    "MaterializedPluginSet",
    "NativePluginResolver",
    "NormalizedJson",
    "NormalizedStr",
    "PluginAgentDefinition",
    "PluginAgentSnapshot",
    "PluginComponent",
    "PluginConfigIssue",
    "PluginConnectorCatalog",
    "PluginConnectorDefinition",
    "PluginConnectorSnapshot",
    "PluginDescriptor",
    "PluginDiagnostic",
    "PluginDiagnosticCode",
    "PluginEncodingError",
    "PluginHookSnapshot",
    "PluginKnowledgeDefinition",
    "PluginKnowledgeSnapshot",
    "PluginLibraryDefinition",
    "PluginLibrarySnapshot",
    "PluginMCPDiscovery",
    "PluginMCPDiscoveryError",
    "PluginMCPServerDefinition",
    "PluginMaterializer",
    "PluginPathOutsideRootError",
    "PluginPathRef",
    "PluginResolver",
    "PluginRouteKey",
    "PluginRouteStatus",
    "PluginSkillSnapshot",
    "PluginSnapshotEntry",
    "PluginSnapshotIdentityError",
    "PluginSourceFormat",
    "PluginSourceFormatUnsupportedError",
    "PluginSourceKind",
    "PluginSourceRef",
    "PluginToolCatalog",
    "PluginToolDefinition",
    "PluginToolExposure",
    "PluginToolGroup",
    "PluginToolGroupSnapshot",
    "PluginToolRoute",
    "PluginToolRouteSnapshot",
    "PluginToolSnapshot",
    "PluginUnsupportedComponent",
    "RegistryConnectorCatalog",
    "RegistryMCPDiscovery",
    "ResolvedPluginSet",
    "ResolvedPluginSnapshot",
    "ToolGroupIdentity",
    "build_plugin_snapshot",
    "build_snapshot",
    "build_tool_catalog",
    "canonical_json",
    "canonical_json_digest",
    "digest_plugin_tree",
    "hook_environment_names",
    "identifier_segment",
    "mcp_server_secrets",
    "normalize_json",
    "normalize_nfc",
    "plugin_identity",
    "plugin_mcp_group_name",
    "plugin_path_ref",
    "plugin_route_source",
    "plugin_runtime_state_names",
    "plugin_skill_runtime_path",
    "plugin_skill_translation",
    "reconcile_plugin_routes",
    "redact_argv",
    "redact_failure",
    "redact_names",
    "redact_url",
    "redact_values",
    "resolve_plugin_path",
    "resolve_tool_group_names",
    "retain_pinned_tools",
    "route_key",
    "sanitize_message",
    "snapshot_bytes",
    "snapshot_digest",
    "sort_diagnostics",
    "surviving_plugin_names",
    "tool_function_name",
    "validate_resolved_plugin_snapshot",
]
