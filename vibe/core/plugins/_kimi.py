from __future__ import annotations

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

_PLUGIN_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_HOOK_TIMEOUT_SECONDS = 600


class _KimiSessionStart(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    skill: str


class _KimiManifest(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=64)
    version: str | None = None
    description: str | None = None
    keywords: list[str] | None = None
    author: JsonValue = None
    homepage: str | None = None
    license: str | None = None
    skills: JsonValue = None
    commands: JsonValue = None
    hooks: JsonValue = None
    mcp_servers: JsonValue = Field(default=None, alias="mcpServers")
    session_start: _KimiSessionStart | None = Field(default=None, alias="sessionStart")
    skill_instructions: str | None = Field(default=None, alias="skillInstructions")
    interface: JsonValue = None


class KimiPluginAdapter:
    source_format = DetectedPluginFormat.KIMI_CODE

    def adapt(
        self, *, root: Path, data_root_base: Path, scope: SkillScope
    ) -> PluginAdapterResult:
        root_manifest = root / "kimi.plugin.json"
        nested_manifest = root / ".kimi-plugin" / "plugin.json"
        manifest_path = root_manifest if root_manifest.is_file() else nested_manifest
        diagnostics: list[PluginAdapterDiagnostic] = []
        if root_manifest.is_file() and nested_manifest.is_file():
            diagnostics.append(
                _diagnostic(
                    "manifest_shadowed",
                    nested_manifest,
                    "kimi.plugin.json takes precedence over .kimi-plugin/plugin.json.",
                    component="manifest",
                    severity="info",
                )
            )
        try:
            relative = manifest_path.relative_to(root).as_posix()
            resolved_manifest = resolve_declared_path(root, f"./{relative}")
            raw_manifest = json.loads(
                read_safe(resolved_manifest, raise_on_error=True).text
            )
            manifest = _KimiManifest.model_validate(raw_manifest)
            if not _PLUGIN_NAME_PATTERN.fullmatch(manifest.name):
                raise ValueError("name must match [a-z0-9][a-z0-9_-]{0,63}")
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
            diagnostics.append(
                PluginAdapterDiagnostic(
                    severity="error",
                    code="plugin.compatibility.kimi_code.manifest_invalid",
                    path=manifest_path,
                    message=f"Failed to load Kimi plugin manifest: {error}",
                    fatal=True,
                    component="manifest",
                )
            )
            return PluginAdapterResult(package=None, diagnostics=tuple(diagnostics))

        namespace = typescript_identifier(manifest.name)
        data_root = data_root_base / namespace
        skills = list(self._skills(root, manifest, diagnostics))
        if manifest.skill_instructions:
            skills = [
                skill.__class__(
                    source_name=skill.source_name,
                    description=skill.description,
                    prompt=(f"{manifest.skill_instructions.strip()}\n\n{skill.prompt}"),
                    source_path=skill.source_path,
                    allowed_tools=skill.allowed_tools,
                    user_invocable=skill.user_invocable,
                    license=skill.license,
                    compatibility=skill.compatibility,
                    metadata=skill.metadata,
                    translation=skill.translation,
                )
                for skill in skills
            ]
        commands = self._commands(root, manifest, diagnostics)
        mcp_servers = self._mcp_servers(
            root, data_root, resolved_manifest, manifest, diagnostics
        )
        unsupported: list[AdaptedUnsupportedComponent] = []
        hooks = _adapt_kimi_hooks(
            resolved_manifest, manifest.hooks, namespace, diagnostics, unsupported
        )
        self._report_unsupported(resolved_manifest, manifest, diagnostics, unsupported)

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
                private_metadata=MappingProxyType({"kimiManifest": raw_manifest}),
                author=author_display_name(manifest.author),
                adapted_skills=(*skills, *commands),
                adapted_hooks=hooks,
            ),
            diagnostics=tuple(diagnostics),
            unsupported_components=tuple(unsupported),
        )

    @staticmethod
    def _skills(
        root: Path, manifest: _KimiManifest, diagnostics: list[PluginAdapterDiagnostic]
    ) -> tuple:
        paths = declared_paths(manifest.skills)
        if paths is None:
            diagnostics.append(
                _diagnostic(
                    "skills_declaration_invalid",
                    root / "kimi.plugin.json",
                    "Kimi skills must be a relative path string or array.",
                    component="skill",
                    severity="error",
                )
            )
            return ()
        if manifest.skills is None and (root / "SKILL.md").is_file():
            paths = ("./SKILL.md",)

        files: set[Path] = set()
        for value in paths:
            if not value.startswith("./"):
                diagnostics.append(
                    _diagnostic(
                        "skill_path_invalid",
                        root / value,
                        "Kimi skill paths must start with './'.",
                        component="skill",
                        severity="error",
                    )
                )
                continue
            try:
                found = skill_files_for_declared_path(root, value)
                if not found:
                    raise ValueError("skill path contains no SKILL.md")
                files.update(found)
            except (OSError, ValueError) as error:
                diagnostics.append(
                    _diagnostic(
                        "skill_path_invalid",
                        root / value.removeprefix("./"),
                        f"Failed to import Kimi skill: {error}",
                        component="skill",
                        severity="warning",
                    )
                )
        return tuple(
            skill
            for path in sorted(files)
            if (
                skill := adapt_skill_file(
                    path=path,
                    source_format="kimi_code",
                    diagnostics=diagnostics,
                    normalize_vendor_fields=True,
                )
            )
            is not None
        )

    @staticmethod
    def _commands(
        root: Path, manifest: _KimiManifest, diagnostics: list[PluginAdapterDiagnostic]
    ) -> tuple:
        paths = declared_paths(manifest.commands)
        if paths is None:
            diagnostics.append(
                _diagnostic(
                    "commands_declaration_invalid",
                    root / "kimi.plugin.json",
                    "Kimi commands must be a relative path string or array.",
                    component="command",
                    severity="error",
                )
            )
            return ()
        commands = []
        for value in paths:
            if not value.startswith("./"):
                diagnostics.append(
                    _diagnostic(
                        "command_path_invalid",
                        root / value,
                        "Kimi command paths must start with './'.",
                        component="command",
                        severity="warning",
                    )
                )
                continue
            try:
                files = markdown_files_for_declared_path(root, value)
                base = resolve_declared_path(root, value)
                base = base if base.is_dir() else base.parent
            except (OSError, ValueError) as error:
                diagnostics.append(
                    _diagnostic(
                        "command_path_invalid",
                        root / value.removeprefix("./"),
                        f"Failed to import Kimi command: {error}",
                        component="command",
                        severity="warning",
                    )
                )
                continue
            for path in files:
                command = adapt_static_command(
                    path=path,
                    source_name=path.relative_to(base).with_suffix("").as_posix(),
                    source_format="kimi_code",
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
        manifest: _KimiManifest,
        diagnostics: list[PluginAdapterDiagnostic],
    ) -> tuple:
        mapping = mcp_mapping(manifest.mcp_servers)
        if manifest.mcp_servers is not None and mapping is None:
            diagnostics.append(
                _diagnostic(
                    "mcp_declaration_invalid",
                    manifest_path,
                    "Kimi mcpServers must be an object.",
                    component="mcp",
                    severity="error",
                )
            )
            return ()
        if mapping is None:
            return ()
        return adapt_mcp_servers(
            root=root,
            data_root=data_root,
            raw_servers=mapping,
            config_path=manifest_path,
            source_format="kimi_code",
            diagnostics=diagnostics,
            placeholders={"${KIMI_PLUGIN_ROOT}": str(root)},
        )

    @staticmethod
    def _report_unsupported(
        manifest_path: Path,
        manifest: _KimiManifest,
        diagnostics: list[PluginAdapterDiagnostic],
        unsupported: list[AdaptedUnsupportedComponent],
    ) -> None:
        if manifest.session_start is not None:
            diagnostics.append(
                _diagnostic(
                    "session_start_unsupported",
                    manifest_path,
                    "Kimi sessionStart.skill is retained privately but automatic session-start skill loading is not supported.",
                    component="session_start",
                    severity="info",
                )
            )
            unsupported.append(
                AdaptedUnsupportedComponent(
                    kind="session_start",
                    path=manifest_path,
                    reason="kimi_session_start_unsupported",
                )
            )
        if manifest.interface is not None:
            diagnostics.append(
                _diagnostic(
                    "install_ui_unsupported",
                    manifest_path,
                    "Kimi installation and interface metadata is retained privately but has no Vibe CLI UI equivalent.",
                    component="interface",
                    severity="info",
                )
            )

        extras = sorted(manifest.model_extra or {})
        if extras:
            diagnostics.append(
                _diagnostic(
                    "fields_unsupported",
                    manifest_path,
                    "Unsupported Kimi manifest fields were retained privately: "
                    + ", ".join(extras),
                    component="manifest",
                    severity="info",
                )
            )
            unsupported.append(
                AdaptedUnsupportedComponent(
                    kind="manifest_fields",
                    path=manifest_path,
                    reason="kimi_manifest_fields_unsupported",
                )
            )


def _adapt_kimi_hooks(
    manifest_path: Path,
    raw_hooks: JsonValue,
    namespace: str,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> tuple[AdaptedHook, ...]:
    if raw_hooks is None:
        return ()
    if not isinstance(raw_hooks, list):
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                manifest_path,
                "Kimi hooks must be an array of declarative hook rules.",
                component="hook",
                severity="error",
            )
        )
        return ()
    adapted: list[AdaptedHook] = []
    for rule_index, raw_rule in enumerate(raw_hooks):
        if not isinstance(raw_rule, dict):
            diagnostics.append(
                _diagnostic(
                    "hooks_invalid",
                    manifest_path,
                    f"Kimi hook rule {rule_index} must be an object.",
                    component="hook",
                    severity="error",
                )
            )
            continue
        unknown_fields = sorted(
            set(raw_rule) - {"event", "matcher", "command", "timeout"}
        )
        if unknown_fields:
            diagnostics.append(
                _diagnostic(
                    "hooks_invalid",
                    manifest_path,
                    f"Kimi hook rule {rule_index} contains unsupported fields: "
                    + ", ".join(unknown_fields),
                    component="hook",
                    severity="warning",
                )
            )
            unsupported.append(
                AdaptedUnsupportedComponent(
                    kind="hooks",
                    path=manifest_path,
                    reason="kimi_hook_rule_unsupported",
                )
            )
            continue
        event = raw_rule.get("event") or raw_rule.get("hookEventName")
        hook_type = _kimi_hook_type(event)
        if hook_type is None:
            _unsupported_kimi_hook(
                manifest_path,
                f"Kimi hook event {event!r} has no equivalent generic Runtime lifecycle.",
                "kimi_hook_lifecycle_unsupported",
                diagnostics,
                unsupported,
            )
            continue
        matcher = _kimi_matcher(raw_rule.get("matcher"), namespace)
        if matcher is None:
            _unsupported_kimi_hook(
                manifest_path,
                f"Kimi matcher {raw_rule.get('matcher')!r} cannot be mapped to canonical Runtime tool names.",
                "kimi_hook_matcher_unsupported",
                diagnostics,
                unsupported,
            )
            continue
        adapted_hook = _adapt_kimi_hook_rule(
            manifest_path, event, hook_type, matcher, rule_index, raw_rule, diagnostics
        )
        if adapted_hook is not None:
            adapted.append(adapted_hook)
    return tuple(adapted)


def _kimi_hook_type(value: object) -> HookType | None:
    if value == "PreToolUse":
        return HookType.PRE_TOOL
    if value == "PostToolUse":
        return HookType.POST_TOOL
    if value == "Stop":
        return HookType.POST_AGENT
    return None


def _kimi_matcher(value: object, _namespace: str) -> str | None:
    if value is None or (isinstance(value, str) and value in {"", "*"}):
        return "*"
    if not isinstance(value, str):
        return None
    try:
        re.compile(value)
    except re.error:
        return None
    return value


def _adapt_kimi_hook_rule(
    manifest_path: Path,
    event: object,
    hook_type: HookType,
    matcher: str,
    rule_index: int,
    action: dict,
    diagnostics: list[PluginAdapterDiagnostic],
) -> AdaptedHook | None:
    command = action.get("command")
    if not isinstance(command, str) or not command.strip():
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                manifest_path,
                "Kimi command hook requires a non-empty command.",
                component="hook",
                severity="error",
            )
        )
        return None
    timeout = action.get("timeout")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 1 <= timeout <= _MAX_HOOK_TIMEOUT_SECONDS
    ):
        diagnostics.append(
            _diagnostic(
                "hooks_invalid",
                manifest_path,
                "Kimi command hook timeout must be an integer between 1 and 600 seconds.",
                component="hook",
                severity="error",
            )
        )
        return None
    name = f"kimi-{str(event).lower()}-{rule_index}"
    return AdaptedHook(
        config=HookConfig(
            name=name,
            type=hook_type,
            command=command,
            match=matcher if hook_type is not HookType.POST_AGENT else None,
            timeout=float(timeout) if timeout is not None else 30.0,
        ),
        source_path=manifest_path,
        protocol=HookProtocol.KIMI_CODE,
    )


def _unsupported_kimi_hook(
    manifest_path: Path,
    message: str,
    reason: str,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> None:
    diagnostics.append(
        _diagnostic(
            "hooks_partially_unsupported",
            manifest_path,
            message,
            component="hook",
            severity="warning",
        )
    )
    unsupported.append(
        AdaptedUnsupportedComponent(kind="hooks", path=manifest_path, reason=reason)
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
        code=f"plugin.compatibility.kimi_code.{suffix}",
        path=path,
        message=message,
        fatal=False,
        component=component,
    )
