"""Session-lifetime plugin pinning for the Vibe Session Runtime.

Nothing here understands a plugin. A plugin is a directory tree that gets
digested, stored, and rebuilt, plus an opaque snapshot blob the Host hands over
and gets back verbatim. Every plugin format, adapter, and snapshot field lives
in the host application.
"""

from __future__ import annotations

from mistralai_vibe_local_harness.vibe.plugins._session import (
    RELEASE_TIMEOUT_SECONDS,
    DeclaredAgentTypeProfile,
    PinnedPackage,
    PinnedPlugins,
    PluginContextDefinition,
    PluginPinMismatch,
    PluginRestoreDiagnostic,
    PluginRestoreDiagnosticCode,
    PluginRestoreError,
    RestoredPlugins,
    SessionPluginBinder,
    SessionPluginBinding,
    SessionPluginProjection,
    SessionPluginProvider,
    empty_plugin_binding,
)
from mistralai_vibe_local_harness.vibe.plugins._store import (
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_ENTRIES,
    IgnoredNames,
    PluginBlobUnavailable,
    PluginPackageCorrupt,
    PluginPackageError,
    PluginPackageInvalidTree,
    PluginPackageMismatch,
    PluginPackageStore,
    PluginPackageTooLarge,
    PluginPackageUnavailable,
    digest_plugin_tree,
)

__all__ = [
    "MAX_PACKAGE_BYTES",
    "MAX_PACKAGE_ENTRIES",
    "RELEASE_TIMEOUT_SECONDS",
    "DeclaredAgentTypeProfile",
    "IgnoredNames",
    "PinnedPackage",
    "PinnedPlugins",
    "PluginBlobUnavailable",
    "PluginContextDefinition",
    "PluginPackageCorrupt",
    "PluginPackageError",
    "PluginPackageInvalidTree",
    "PluginPackageMismatch",
    "PluginPackageStore",
    "PluginPackageTooLarge",
    "PluginPackageUnavailable",
    "PluginPinMismatch",
    "PluginRestoreDiagnostic",
    "PluginRestoreDiagnosticCode",
    "PluginRestoreError",
    "RestoredPlugins",
    "SessionPluginBinder",
    "SessionPluginBinding",
    "SessionPluginProjection",
    "SessionPluginProvider",
    "digest_plugin_tree",
    "empty_plugin_binding",
]
