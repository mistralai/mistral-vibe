from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import shutil
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vibe.core.plugins._compatibility import (
    DetectedPluginFormat,
    PluginFormatDetection as _FormatDetection,
    detect_plugin_source_format,
)
from vibe.core.plugins._native import PluginConfigIssue


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class _InspectionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel, extra="forbid", frozen=True, populate_by_name=True
    )


class PluginDiagnostic(_InspectionModel):
    severity: Literal["info", "warning", "error"]
    code: str
    path: str
    message: str
    fatal: bool
    plugin_name: str | None = None
    source_format: DetectedPluginFormat | None = None
    component: str | None = None


class InspectedPlugin(_InspectionModel):
    name: str
    description: str
    namespace: str
    version: str | None = None


class InspectedSkill(_InspectionModel):
    name: str
    source_name: str
    description: str
    path: str
    user_invocable: bool
    allowed_tools: tuple[str, ...]
    translation: Literal["synthetic_skill"] | None = None


class InspectedTool(_InspectionModel):
    name: str
    description: str
    exposure: Literal["programmatic", "direct", "direct_and_programmatic"]


class InspectedToolGroup(_InspectionModel):
    name: str
    description: str
    tools: tuple[InspectedTool, ...]


class InspectedHook(_InspectionModel):
    name: str
    path: str


class InspectedKnowledge(_InspectionModel):
    name: str
    source_name: str
    description: str
    path: str


class InspectedAgent(_InspectionModel):
    name: str
    source_name: str
    description: str
    path: str


class InspectedLibrary(_InspectionModel):
    language: Literal["node", "python"]
    alias: str
    path: str


class InspectedConnector(_InspectionModel):
    id: str
    tools: tuple[str, ...]
    path: str


class UnsupportedPluginComponent(_InspectionModel):
    kind: str
    path: str
    reason: str


class PluginInspection(_InspectionModel):
    schema_version: Literal[1] = 1
    source_format: DetectedPluginFormat
    valid: bool
    plugin: InspectedPlugin | None
    skills: tuple[InspectedSkill, ...]
    tool_groups: tuple[InspectedToolGroup, ...] = ()
    hooks: tuple[InspectedHook, ...] = ()
    knowledge: tuple[InspectedKnowledge, ...] = ()
    agents: tuple[InspectedAgent, ...] = ()
    libraries: tuple[InspectedLibrary, ...] = ()
    connectors: tuple[InspectedConnector, ...] = ()
    unsupported_components: tuple[UnsupportedPluginComponent, ...] = ()
    diagnostics: tuple[PluginDiagnostic, ...]
    content_digest: str | None = None

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, exclude_none=True, indent=2)


class PluginInspectionError(ValueError):
    pass


def inspect_plugin(path: Path) -> PluginInspection:
    root = _resolve_plugin_root(path)
    detection = _detect_source_format(root)
    if detection.unsupported_schema is not None:
        inspection = _unsupported_schema_inspection(detection)
    else:
        match detection.source_format:
            case DetectedPluginFormat.AMBIGUOUS:
                inspection = _ambiguous_format_inspection(root, detection.marker_paths)
            case (
                DetectedPluginFormat.AGENT_PLUGINS_1_0
                | DetectedPluginFormat.CODEX
                | DetectedPluginFormat.CLAUDE_CODE
                | DetectedPluginFormat.KIMI_CODE
                | DetectedPluginFormat.OPENCODE
            ):
                inspection = _inspect_with_native_resolver(
                    root, detection.source_format
                )
            case _:
                inspection = _unknown_format_inspection()
    return inspection


def _resolve_plugin_root(path: Path) -> Path:
    try:
        root = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PluginInspectionError(
            f"Plugin path cannot be resolved: {error}"
        ) from error
    if not root.is_dir():
        raise PluginInspectionError(f"Plugin path is not a directory: {root}")
    return root


def _detect_source_format(root: Path) -> _FormatDetection:
    return detect_plugin_source_format(root)


def _unsupported_schema_inspection(detection: _FormatDetection) -> PluginInspection:
    schema = detection.unsupported_schema
    assert schema is not None
    diagnostic = PluginDiagnostic(
        severity="error",
        code="plugin.schema.version_unsupported",
        path="plugin.json",
        message=f"Agent Plugins schema version is not supported: {schema}",
        fatal=True,
        source_format=detection.source_format,
        component="manifest",
    )
    return _empty_inspection(detection.source_format, (diagnostic,))


def _ambiguous_format_inspection(
    root: Path, marker_paths: tuple[Path, ...]
) -> PluginInspection:
    paths = tuple(_relative_path(path, root) for path in marker_paths)
    diagnostic = PluginDiagnostic(
        severity="error",
        code="plugin.compatibility.format_ambiguous",
        path=".",
        message=f"Multiple plugin formats were detected: {', '.join(paths)}",
        fatal=True,
        source_format=DetectedPluginFormat.AMBIGUOUS,
        component="manifest",
    )
    return _empty_inspection(DetectedPluginFormat.AMBIGUOUS, (diagnostic,))


def _unavailable_adapter_inspection(
    root: Path, detection: _FormatDetection
) -> PluginInspection:
    marker = _relative_path(detection.marker_paths[0], root)
    diagnostic = PluginDiagnostic(
        severity="error",
        code="plugin.compatibility.adapter_unavailable",
        path=marker,
        message=(
            f"The {detection.source_format.value} plugin format was detected, but its "
            "compatibility adapter is not available in this build."
        ),
        fatal=True,
        source_format=detection.source_format,
        component="manifest",
    )
    unsupported = UnsupportedPluginComponent(
        kind="source_format", path=marker, reason="compatibility_adapter_unavailable"
    )
    return _empty_inspection(
        detection.source_format, (diagnostic,), unsupported_components=(unsupported,)
    )


def _unknown_format_inspection() -> PluginInspection:
    diagnostic = PluginDiagnostic(
        severity="error",
        code="plugin.compatibility.format_unrecognized",
        path=".",
        message="No supported plugin manifest was found.",
        fatal=True,
        source_format=DetectedPluginFormat.UNKNOWN,
        component="manifest",
    )
    return _empty_inspection(DetectedPluginFormat.UNKNOWN, (diagnostic,))


def _empty_inspection(
    source_format: DetectedPluginFormat,
    diagnostics: tuple[PluginDiagnostic, ...],
    *,
    unsupported_components: tuple[UnsupportedPluginComponent, ...] = (),
) -> PluginInspection:
    return PluginInspection(
        source_format=source_format,
        valid=False,
        plugin=None,
        skills=(),
        unsupported_components=unsupported_components,
        diagnostics=diagnostics,
    )


def _inspect_with_native_resolver(
    root: Path, source_format: DetectedPluginFormat
) -> PluginInspection:
    from vibe.core.plugins import PluginResolver

    with _isolated_plugin_root(root) as staged_root:
        resolution = PluginResolver(
            project_roots=[staged_root.parent],
            data_root_base=staged_root.parent.parent / "plugin-data",
        ).resolve()
        descriptor = resolution.plugins[0] if resolution.plugins else None
        plugin = None
        if descriptor is not None:
            plugin = InspectedPlugin(
                name=descriptor.name,
                description=descriptor.description,
                namespace=descriptor.namespace,
                version=descriptor.version,
            )
        skills = tuple(
            _inspect_skill(skill, root, staged_root)
            for _, skill in sorted(resolution.skills.items())
        )
        hooks = tuple(
            InspectedHook(
                name=hook.declared_name or hook.config.name,
                path=_relative_path(
                    _remap_staged_path(hook.config_file, root, staged_root), root
                ),
            )
            for hook in resolution.runtime_hooks
            if hook.config_file is not None
        )
        knowledge = tuple(
            InspectedKnowledge(
                name=definition.name,
                source_name=definition.source_name,
                description=definition.description,
                path=_relative_path(
                    _remap_staged_path(definition.source_entrypoint, root, staged_root),
                    root,
                ),
            )
            for definition in resolution.knowledge
        )
        agents = tuple(
            InspectedAgent(
                name=definition.name,
                source_name=definition.source_name,
                description=definition.profile.description,
                path=_relative_path(
                    _remap_staged_path(definition.source_file, root, staged_root), root
                ),
            )
            for definition in resolution.agents
        )
        libraries = tuple(
            InspectedLibrary(
                language=definition.language,
                alias=definition.alias,
                path=_relative_path(
                    _remap_staged_path(definition.source_path, root, staged_root), root
                ),
            )
            for definition in resolution.libraries
        )
        connectors = tuple(
            InspectedConnector(
                id=definition.source_id,
                tools=definition.tools,
                path=_relative_path(
                    _remap_staged_path(definition.config_file, root, staged_root), root
                ),
            )
            for definition in resolution.connectors
        )
        diagnostics = tuple(
            _diagnostic_from_issue(
                issue,
                root=root,
                staged_root=staged_root,
                plugin_name=plugin.name if plugin is not None else None,
                source_format=source_format,
                fatal=plugin is None,
            )
            for issue in resolution.issues
        )
        unsupported_components = tuple(
            UnsupportedPluginComponent(
                kind=component.kind,
                path=_relative_path(
                    _remap_staged_path(component.path, root, staged_root), root
                ),
                reason=component.reason,
            )
            for component in sorted(
                resolution.unsupported_components,
                key=lambda item: (item.kind, str(item.path)),
            )
        )
        manifest_path = (
            _remap_staged_path(descriptor.manifest_path, root, staged_root)
            if descriptor is not None
            else None
        )
    return PluginInspection(
        source_format=source_format,
        valid=plugin is not None,
        plugin=plugin,
        skills=skills,
        hooks=hooks,
        knowledge=knowledge,
        agents=agents,
        libraries=libraries,
        connectors=connectors,
        unsupported_components=unsupported_components,
        diagnostics=diagnostics,
        content_digest=(
            _content_digest(root, manifest_path, skills)
            if plugin is not None and manifest_path is not None
            else None
        ),
    )


@contextmanager
def _isolated_plugin_root(root: Path) -> Iterator[Path]:
    temporary_directory = Path(tempfile.mkdtemp(prefix="vibe-plugin-inspect-"))
    try:
        staged_root = temporary_directory / ".vibe" / "plugins" / "plugin"
        staged_root.parent.mkdir(parents=True)
        try:
            staged_root.symlink_to(root, target_is_directory=True)
        except (NotImplementedError, OSError):
            try:
                shutil.copytree(root, staged_root, symlinks=True)
            except OSError as error:
                raise PluginInspectionError(
                    f"Plugin package cannot be staged for inspection: {error}"
                ) from error
        yield staged_root
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)


def _inspect_skill(skill: object, root: Path, staged_root: Path) -> InspectedSkill:
    from vibe.core.plugins import plugin_skill_translation
    from vibe.core.skills.models import SkillInfo

    if not isinstance(skill, SkillInfo) or skill.skill_path is None:
        raise PluginInspectionError("Resolved plugin skill is missing its source path.")
    source_name = skill.name.partition(":")[2] or skill.name
    path = _remap_staged_path(skill.skill_path, root, staged_root)
    return InspectedSkill(
        name=skill.name,
        source_name=source_name,
        description=skill.description,
        path=_relative_path(path, root),
        user_invocable=skill.user_invocable,
        allowed_tools=tuple(skill.allowed_tools),
        translation=plugin_skill_translation(skill),
    )


def _diagnostic_from_issue(
    issue: PluginConfigIssue,
    *,
    root: Path,
    staged_root: Path,
    plugin_name: str | None,
    source_format: DetectedPluginFormat,
    fatal: bool,
) -> PluginDiagnostic:
    path = _remap_staged_path(issue.file, root, staged_root)
    relative_path = _relative_path(path, root)
    classified_code, classified_component = _classify_issue(
        relative_path, issue.message
    )
    sanitized_message = _sanitize_message(issue.message, root, staged_root)
    return PluginDiagnostic(
        severity=issue.severity,
        code=issue.code or classified_code,
        path=relative_path,
        message=sanitized_message,
        fatal=issue.fatal if issue.code is not None else fatal,
        plugin_name=plugin_name,
        source_format=source_format,
        component=issue.component or classified_component,
    )


def _classify_issue(path: str, message: str) -> tuple[str, str]:
    lowered = message.lower()
    if "inside the plugin root" in lowered or "resolve inside" in lowered:
        result = ("plugin.path.outside_root", _component_for_path(path))
    elif "namespace" in lowered and "reserved" in lowered:
        result = ("plugin.namespace.reserved", "manifest")
    elif "namespace" in lowered and "collides" in lowered:
        result = ("plugin.namespace.collision", "manifest")
    elif "duplicate plugin name" in lowered:
        result = ("plugin.name.collision", "manifest")
    elif path == "plugin.json":
        result = ("plugin.manifest.invalid", "manifest")
    elif path.startswith("skills/"):
        result = ("plugin.skill.invalid", "skill")
    else:
        result = ("plugin.filesystem.error", _component_for_path(path))
    return result


def _component_for_path(path: str) -> str:
    if path == "plugin.json":
        return "manifest"
    if path == "skills" or path.startswith("skills/"):
        return "skill"
    return "package"


def _sanitize_message(message: str, root: Path, staged_root: Path) -> str:
    sanitized = message.replace(str(staged_root), "<plugin>")
    sanitized = sanitized.replace(str(staged_root.parent), "<staging>")
    sanitized = sanitized.replace(str(root), "<plugin>")
    return re.sub(
        r"input_value=.*?,\s*input_type=",
        "input_value=<redacted>, input_type=",
        sanitized,
        flags=re.DOTALL,
    )


def _remap_staged_path(path: Path, root: Path, staged_root: Path) -> Path:
    for base in (staged_root, staged_root.resolve()):
        try:
            return root / path.relative_to(base)
        except ValueError:
            continue
    return path


def _relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return "<outside-plugin>"
    value = relative.as_posix()
    return value if value else "."


def _content_digest(
    root: Path, manifest_path: Path, skills: tuple[InspectedSkill, ...]
) -> str:
    digest = hashlib.sha256()
    paths = [manifest_path.relative_to(root), *(Path(skill.path) for skill in skills)]
    for relative in sorted(paths):
        path = root / relative
        try:
            content = path.read_bytes()
        except OSError as error:
            raise PluginInspectionError(f"Cannot hash {relative}: {error}") from error
        encoded_path = relative.as_posix().encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"
