from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from vibe.core.hooks.models import HookConfig, HookProtocol, HookType
from vibe.core.plugins._compatibility import (
    AdaptedHook,
    AdaptedPluginPackage,
    AdaptedUnsupportedComponent,
    DetectedPluginFormat,
    PluginAdapterDiagnostic,
    PluginAdapterResult,
    author_display_name,
    resolve_declared_path,
    typescript_identifier,
)
from vibe.core.plugins._foreign import (
    adapt_mcp_servers,
    adapt_skill_file,
    adapt_static_command,
    declared_paths,
    markdown_files_for_declared_path,
    mcp_mapping,
    skill_files_for_declared_path,
)
from vibe.core.skills.models import SkillScope
from vibe.utils.io import read_safe

_PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class _ClaudeManifest(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=256)
    schema_id: str | None = Field(default=None, alias="$schema")
    display_name: str | None = Field(default=None, alias="displayName")
    version: str | None = None
    description: str | None = None
    author: JsonValue = None
    homepage: str | None = None
    repository: JsonValue = None
    license: str | None = None
    keywords: list[str] | None = None
    skills: JsonValue = None
    commands: JsonValue = None
    hooks: JsonValue = None
    mcp_servers: JsonValue = Field(default=None, alias="mcpServers")
    agents: JsonValue = None
    lsp_servers: JsonValue = Field(default=None, alias="lspServers")
    output_styles: JsonValue = Field(default=None, alias="outputStyles")
    workflows: JsonValue = None
    settings: JsonValue = None
    experimental: JsonValue = None
    interface: JsonValue = None
    default_enabled: JsonValue = Field(default=None, alias="defaultEnabled")
    dependencies: JsonValue = None
    user_config: JsonValue = Field(default=None, alias="userConfig")
    channels: JsonValue = None


class ClaudePluginAdapter:
    source_format = DetectedPluginFormat.CLAUDE_CODE

    def adapt(
        self, *, root: Path, data_root_base: Path, scope: SkillScope
    ) -> PluginAdapterResult:
        manifest_path = root / ".claude-plugin" / "plugin.json"
        try:
            resolved_manifest = resolve_declared_path(
                root, ".claude-plugin/plugin.json"
            )
            raw_manifest = json.loads(
                read_safe(resolved_manifest, raise_on_error=True).text
            )
            manifest = _ClaudeManifest.model_validate(raw_manifest)
            if not _PLUGIN_NAME_PATTERN.fullmatch(manifest.name):
                raise ValueError("name must match ^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
            if manifest.version is not None and not _SEMVER_PATTERN.fullmatch(
                manifest.version
            ):
                raise ValueError("version must be a semantic version")
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
            return PluginAdapterResult(
                package=None,
                diagnostics=(
                    PluginAdapterDiagnostic(
                        severity="error",
                        code="plugin.compatibility.claude_code.manifest_invalid",
                        path=manifest_path,
                        message=f"Failed to load Claude plugin manifest: {error}",
                        fatal=True,
                        component="manifest",
                    ),
                ),
            )

        diagnostics: list[PluginAdapterDiagnostic] = []
        unsupported: list[AdaptedUnsupportedComponent] = []
        namespace = typescript_identifier(manifest.name)
        data_root = data_root_base / namespace
        skills = self._skills(root, manifest, diagnostics)
        commands = self._commands(root, manifest, diagnostics)
        mcp_servers = self._mcp_servers(
            root, data_root, resolved_manifest, manifest, diagnostics
        )
        hooks = _adapt_claude_hooks(
            root, resolved_manifest, manifest.hooks, namespace, diagnostics, unsupported
        )
        self._report_unsupported_components(
            root, resolved_manifest, manifest, diagnostics, unsupported
        )

        return PluginAdapterResult(
            package=AdaptedPluginPackage(
                source_format=self.source_format,
                manifest_path=resolved_manifest,
                name=manifest.name,
                version=manifest.version,
                description=manifest.description
                or f"Capabilities provided by {manifest.name}.",
                namespace=namespace,
                data_root=data_root,
                scope=scope,
                skill_roots=(),
                mcp_servers=mcp_servers,
                tool_overrides=MappingProxyType({}),
                private_metadata=MappingProxyType({"claudeManifest": raw_manifest}),
                author=author_display_name(manifest.author),
                adapted_skills=(*skills, *commands),
                adapted_hooks=hooks,
            ),
            diagnostics=tuple(diagnostics),
            unsupported_components=tuple(unsupported),
        )

    @staticmethod
    def _skills(
        root: Path,
        manifest: _ClaudeManifest,
        diagnostics: list[PluginAdapterDiagnostic],
    ) -> tuple:
        paths = declared_paths(manifest.skills)
        if paths is None:
            diagnostics.append(
                _diagnostic(
                    "skills_declaration_invalid",
                    root / ".claude-plugin" / "plugin.json",
                    "Claude skills must be a relative path string or array.",
                    component="skill",
                    severity="error",
                )
            )
            paths = ()
        conventional = root / "skills"
        if conventional.is_dir():
            paths = (*paths, "./skills/")
        elif not paths and (root / "SKILL.md").is_file():
            paths = ("./SKILL.md",)

        files: set[Path] = set()
        for value in paths:
            if not value.startswith("./"):
                diagnostics.append(
                    _diagnostic(
                        "skill_path_invalid",
                        root / value,
                        "Claude skill paths must start with './'.",
                        component="skill",
                        severity="error",
                    )
                )
                continue
            try:
                files.update(skill_files_for_declared_path(root, value))
            except (OSError, ValueError) as error:
                diagnostics.append(
                    _diagnostic(
                        "skill_path_invalid",
                        root / value.removeprefix("./"),
                        f"Failed to import Claude skills: {error}",
                        component="skill",
                        severity="error",
                    )
                )
        return tuple(
            skill
            for path in sorted(files)
            if (
                skill := adapt_skill_file(
                    path=path,
                    source_format="claude_code",
                    diagnostics=diagnostics,
                    normalize_vendor_fields=False,
                )
            )
            is not None
        )

    @staticmethod
    def _commands(
        root: Path,
        manifest: _ClaudeManifest,
        diagnostics: list[PluginAdapterDiagnostic],
    ) -> tuple:
        paths = declared_paths(manifest.commands)
        if paths is None:
            diagnostics.append(
                _diagnostic(
                    "commands_declaration_invalid",
                    root / ".claude-plugin" / "plugin.json",
                    "Claude commands must be a relative path string or array.",
                    component="command",
                    severity="error",
                )
            )
            return ()
        if (root / "commands").is_dir():
            paths = (*paths, "./commands/")

        commands = []
        for value in paths:
            if not value.startswith("./"):
                diagnostics.append(
                    _diagnostic(
                        "command_path_invalid",
                        root / value,
                        "Claude command paths must start with './'.",
                        component="command",
                        severity="error",
                    )
                )
                continue
            try:
                files = markdown_files_for_declared_path(root, value)
            except (OSError, ValueError) as error:
                diagnostics.append(
                    _diagnostic(
                        "command_path_invalid",
                        root / value.removeprefix("./"),
                        f"Failed to import Claude commands: {error}",
                        component="command",
                        severity="error",
                    )
                )
                continue
            base = resolve_declared_path(root, value)
            base = base if base.is_dir() else base.parent
            for path in files:
                source_name = path.relative_to(base).with_suffix("").as_posix()
                command = adapt_static_command(
                    path=path,
                    source_name=source_name,
                    source_format="claude_code",
                    diagnostics=diagnostics,
                )
                if command is not None:
                    commands.append(command)
        return tuple(commands)

    @staticmethod
    def _mcp_servers(
        root: Path,
        data_root: Path,
        manifest_path: Path,
        manifest: _ClaudeManifest,
        diagnostics: list[PluginAdapterDiagnostic],
    ) -> tuple:
        sources: list[tuple[Path, Mapping[str, object]]] = []
        implicit = root / ".mcp.json"
        if implicit.is_file():
            loaded = _load_mcp_file(root, "./.mcp.json", diagnostics)
            if loaded is not None:
                sources.append(loaded)
        declared = manifest.mcp_servers
        if isinstance(declared, Mapping):
            mapping = mcp_mapping(declared)
            if mapping is not None:
                sources.append((manifest_path, mapping))
        else:
            paths = declared_paths(declared)
            if paths is None:
                diagnostics.append(
                    _diagnostic(
                        "mcp_declaration_invalid",
                        manifest_path,
                        "Claude mcpServers must be an object, path, or array of paths.",
                        component="mcp",
                        severity="error",
                    )
                )
            else:
                for value in paths:
                    loaded = _load_mcp_file(root, value, diagnostics)
                    if loaded is not None and loaded[0] not in {
                        source[0] for source in sources
                    }:
                        sources.append(loaded)
        return tuple(
            server
            for path, mapping in sources
            for server in adapt_mcp_servers(
                root=root,
                data_root=data_root,
                raw_servers=mapping,
                config_path=path,
                source_format="claude_code",
                diagnostics=diagnostics,
                placeholders={
                    "${CLAUDE_PLUGIN_ROOT}": str(root),
                    "${CLAUDE_PLUGIN_DATA}": str(data_root),
                },
            )
        )

    @staticmethod
    def _report_unsupported_components(
        root: Path,
        manifest_path: Path,
        manifest: _ClaudeManifest,
        diagnostics: list[PluginAdapterDiagnostic],
        unsupported: list[AdaptedUnsupportedComponent],
    ) -> None:
        components = {
            "agents": (manifest.agents, root / "agents", "agents"),
            "lsp": (manifest.lsp_servers, root / ".lsp.json", "lspServers"),
            "output_styles": (
                manifest.output_styles,
                root / "output-styles",
                "outputStyles",
            ),
            "workflows": (manifest.workflows, root / "workflows", "workflows"),
            "settings": (manifest.settings, root / "settings.json", "settings"),
        }
        for kind, (declared, conventional, field) in components.items():
            if declared is None and not conventional.exists():
                continue
            diagnostics.append(
                _diagnostic(
                    f"{kind}_unsupported",
                    manifest_path if declared is not None else conventional,
                    f"Claude {field} are retained as package data but are not converted into a Unified Harness capability.",
                    component=kind,
                    severity="info",
                )
            )
            unsupported.append(
                AdaptedUnsupportedComponent(
                    kind=kind,
                    path=manifest_path if declared is not None else conventional,
                    reason=f"claude_{kind}_unsupported",
                )
            )

        experimental = manifest.experimental
        if isinstance(experimental, Mapping):
            for field, kind in (("themes", "themes"), ("monitors", "monitors")):
                if field not in experimental:
                    continue
                diagnostics.append(
                    _diagnostic(
                        f"{kind}_unsupported",
                        manifest_path,
                        f"Claude {kind} have no portable Runtime equivalent.",
                        component=kind,
                        severity="info",
                    )
                )
                unsupported.append(
                    AdaptedUnsupportedComponent(
                        kind=kind,
                        path=manifest_path,
                        reason=f"claude_{kind}_unsupported",
                    )
                )

        ui_fields = {
            "interface": manifest.interface,
            "defaultEnabled": manifest.default_enabled,
            "dependencies": manifest.dependencies,
            "userConfig": manifest.user_config,
            "channels": manifest.channels,
        }
        present_ui = sorted(
            field for field, value in ui_fields.items() if value is not None
        )
        if present_ui:
            diagnostics.append(
                _diagnostic(
                    "install_ui_unsupported",
                    manifest_path,
                    "Claude installation, configuration, and UI fields are Runtime-private and unsupported: "
                    + ", ".join(present_ui),
                    component="interface",
                    severity="info",
                )
            )
            unsupported.append(
                AdaptedUnsupportedComponent(
                    kind="install_ui",
                    path=manifest_path,
                    reason="claude_install_ui_unsupported",
                )
            )

        known = {
            field.alias or name for name, field in _ClaudeManifest.model_fields.items()
        }
        extras = sorted(set(manifest.model_extra or {}) - known)
        if extras:
            diagnostics.append(
                _diagnostic(
                    "fields_unsupported",
                    manifest_path,
                    "Unrecognized Claude manifest fields were retained privately: "
                    + ", ".join(extras),
                    component="manifest",
                    severity="info",
                )
            )


def _load_mcp_file(
    root: Path, value: str, diagnostics: list[PluginAdapterDiagnostic]
) -> tuple[Path, Mapping[str, object]] | None:
    try:
        if not value.startswith("./"):
            raise ValueError("Claude component paths must start with './'")
        path = resolve_declared_path(root, value)
        raw = json.loads(read_safe(path, raise_on_error=True).text)
        mapping = mcp_mapping(raw)
        if mapping is None:
            raise ValueError("MCP configuration must be an object")
        return path, mapping
    except (OSError, ValueError, json.JSONDecodeError) as error:
        diagnostics.append(
            _diagnostic(
                "mcp_config_invalid",
                root / value.removeprefix("./"),
                f"Failed to import Claude MCP configuration: {error}",
                component="mcp",
                severity="error",
            )
        )
        return None


def _adapt_claude_hooks(
    root: Path,
    manifest_path: Path,
    declared: JsonValue,
    namespace: str,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> tuple[AdaptedHook, ...]:
    sources = _claude_hook_sources(root, manifest_path, declared, diagnostics)
    return tuple(
        hook
        for path, source in sources
        for hook in _adapt_claude_hook_source(
            path, source, namespace, diagnostics, unsupported
        )
    )


def _claude_hook_sources(
    root: Path,
    manifest_path: Path,
    declared: JsonValue,
    diagnostics: list[PluginAdapterDiagnostic],
) -> tuple[tuple[Path, Mapping[str, object]], ...]:
    sources: list[tuple[Path, Mapping[str, object]]] = []
    conventional = root / "hooks" / "hooks.json"
    if conventional.is_file():
        loaded = _load_claude_hooks_file(root, conventional, diagnostics)
        if loaded is not None:
            sources.append(loaded)

    declared_paths: tuple[str, ...] = ()
    if isinstance(declared, str):
        declared_paths = (declared,)
    elif isinstance(declared, list) and all(
        isinstance(value, str) for value in declared
    ):
        declared_paths = tuple(value for value in declared if isinstance(value, str))
    elif isinstance(declared, Mapping):
        sources.append((manifest_path, declared))
    elif declared is not None:
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                manifest_path,
                "Claude hooks must be an inline object, relative JSON path, or array of relative JSON paths.",
                component="hook",
                severity="error",
            )
        )

    for declared_path in declared_paths:
        try:
            if not declared_path.startswith("./"):
                raise ValueError("Claude hook paths must start with './'")
            resolved = resolve_declared_path(root, declared_path)
            if not resolved.is_file():
                raise ValueError("Claude hook path must resolve to a JSON file")
            loaded = _load_claude_hooks_file(root, resolved, diagnostics)
            if loaded is not None and loaded[0] not in {path for path, _ in sources}:
                sources.append(loaded)
        except (OSError, ValueError) as error:
            diagnostics.append(
                _diagnostic(
                    "hooks_invalid",
                    manifest_path,
                    f"Failed to import Claude hooks: {error}",
                    component="hook",
                    severity="error",
                )
            )
    return tuple(sources)


def _adapt_claude_hook_source(
    path: Path,
    source: Mapping[str, object],
    namespace: str,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> tuple[AdaptedHook, ...]:
    hook_groups = source.get("hooks", source)
    if not isinstance(hook_groups, Mapping):
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                path,
                "Claude hook configuration must contain a hooks object.",
                component="hook",
                severity="error",
            )
        )
        return ()
    return tuple(
        hook
        for event_name, raw_rules in hook_groups.items()
        for hook in _adapt_claude_hook_group(
            path, event_name, raw_rules, namespace, diagnostics, unsupported
        )
    )


def _adapt_claude_hook_group(
    path: Path,
    event_name: object,
    raw_rules: object,
    namespace: str,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> tuple[AdaptedHook, ...]:
    hook_type = _claude_hook_type(event_name)
    if hook_type is None:
        _unsupported_claude_hook(
            path,
            f"Claude hook event {event_name!r} has no equivalent generic Runtime lifecycle.",
            "claude_hook_lifecycle_unsupported",
            diagnostics,
            unsupported,
        )
        return ()
    if not isinstance(raw_rules, list):
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                path,
                f"Claude hook event {event_name!r} must contain an array.",
                component="hook",
                severity="error",
            )
        )
        return ()
    adapted: list[AdaptedHook] = []
    for rule_index, raw_rule in enumerate(raw_rules):
        adapted.extend(
            _adapt_claude_hook_rule(
                path,
                event_name,
                hook_type,
                rule_index,
                raw_rule,
                namespace,
                diagnostics,
                unsupported,
            )
        )
    return tuple(adapted)


def _adapt_claude_hook_rule(
    path: Path,
    event_name: object,
    hook_type: HookType,
    rule_index: int,
    raw_rule: object,
    namespace: str,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> tuple[AdaptedHook, ...]:
    if not isinstance(raw_rule, Mapping):
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                path,
                f"Claude hook rule {event_name}[{rule_index}] must be an object.",
                component="hook",
                severity="error",
            )
        )
        return ()
    matcher = _claude_matcher(raw_rule.get("matcher"), namespace)
    if matcher is None:
        _unsupported_claude_hook(
            path,
            f"Claude matcher {raw_rule.get('matcher')!r} cannot be mapped to canonical Runtime tool names.",
            "claude_hook_matcher_unsupported",
            diagnostics,
            unsupported,
        )
        return ()
    raw_actions = raw_rule.get("hooks")
    if not isinstance(raw_actions, list):
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                path,
                f"Claude hook rule {event_name}[{rule_index}] must contain a hooks array.",
                component="hook",
                severity="error",
            )
        )
        return ()
    return tuple(
        adapted_hook
        for action_index, action in enumerate(raw_actions)
        if (
            adapted_hook := _adapt_claude_hook_action(
                path,
                event_name,
                hook_type,
                matcher,
                rule_index,
                action_index,
                action,
                diagnostics,
                unsupported,
            )
        )
        is not None
    )


def _load_claude_hooks_file(
    root: Path, path: Path, diagnostics: list[PluginAdapterDiagnostic]
) -> tuple[Path, Mapping[str, object]] | None:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("Claude hook file must stay inside the plugin root")
        raw = json.loads(read_safe(resolved, raise_on_error=True).text)
        if not isinstance(raw, Mapping):
            raise ValueError("Claude hook configuration must be an object")
        return resolved, raw
    except (OSError, ValueError, json.JSONDecodeError) as error:
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                path,
                f"Failed to import Claude hooks: {error}",
                component="hook",
                severity="error",
            )
        )
        return None


def _claude_hook_type(value: object) -> HookType | None:
    if value == "PreToolUse":
        return HookType.PRE_TOOL
    if value == "PostToolUse":
        return HookType.POST_TOOL
    if value == "Stop":
        return HookType.POST_AGENT
    return None


def _claude_matcher(value: object, _namespace: str) -> str | None:
    if value is None or (isinstance(value, str) and value in {"", "*"}):
        return "*"
    if not isinstance(value, str):
        return None
    try:
        re.compile(value)
    except re.error:
        return None
    return value


def _adapt_claude_hook_action(
    path: Path,
    event_name: object,
    hook_type: HookType,
    matcher: str,
    rule_index: int,
    action_index: int,
    action: object,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> AdaptedHook | None:
    if not isinstance(action, Mapping):
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                path,
                f"Claude hook action {event_name}[{rule_index}].hooks[{action_index}] must be an object.",
                component="hook",
                severity="error",
            )
        )
        return None
    unsupported_fields = [
        field for field in ("if", "args", "shell", "asyncRewake") if field in action
    ]
    if (
        action.get("type") != "command"
        or action.get("async") is True
        or unsupported_fields
    ):
        _unsupported_claude_hook(
            path,
            "Only synchronous shell-form Claude command hooks without an `if` filter map to generic Runtime hooks.",
            "claude_hook_action_unsupported",
            diagnostics,
            unsupported,
        )
        return None
    command = action.get("command")
    if not isinstance(command, str) or not command.strip():
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                path,
                "Claude command hook requires a non-empty command.",
                component="hook",
                severity="error",
            )
        )
        return None
    timeout = action.get("timeout")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or timeout <= 0
    ):
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                path,
                "Claude command hook timeout must be a positive number.",
                component="hook",
                severity="error",
            )
        )
        return None
    name = f"claude-{str(event_name).lower()}-{rule_index}-{action_index}"
    return AdaptedHook(
        config=HookConfig(
            name=name,
            type=hook_type,
            command=command,
            match=matcher if hook_type is not HookType.POST_AGENT else None,
            timeout=float(timeout) if timeout is not None else 600.0,
        ),
        source_path=path,
        protocol=HookProtocol.CLAUDE_CODE,
    )


def _unsupported_claude_hook(
    path: Path,
    message: str,
    reason: str,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> None:
    diagnostics.append(
        _diagnostic(
            "hooks_partially_unsupported",
            path,
            message,
            component="hook",
            severity="warning",
        )
    )
    unsupported.append(
        AdaptedUnsupportedComponent(kind="hooks", path=path, reason=reason)
    )


def _diagnostic(
    suffix: str,
    path: Path,
    message: str,
    *,
    component: str,
    severity: Literal["info", "warning", "error"],
) -> PluginAdapterDiagnostic:
    return PluginAdapterDiagnostic(
        severity=severity,
        code=f"plugin.compatibility.claude_code.{suffix}",
        path=path,
        message=message,
        fatal=False,
        component=component,
    )
