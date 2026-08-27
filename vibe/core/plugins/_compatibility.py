from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Literal, Protocol

from vibe.core.config import MCPServer
from vibe.core.hooks.models import HookConfig, HookProtocol
from vibe.core.skills.models import SkillScope
from vibe.utils.io import read_safe

_NATIVE_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
_AGENT_PLUGIN_SCHEMA_PREFIX = "https://agent-plugins.org/schemas/"
_MAX_SKILL_NAME_LENGTH = 64
_OPENCODE_CODE_SUFFIXES = {".cjs", ".cts", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx"}


class DetectedPluginFormat(StrEnum):
    AGENT_PLUGINS_1_0 = "agent_plugins_1_0"
    CODEX = "codex"
    CLAUDE_CODE = "claude_code"
    KIMI_CODE = "kimi_code"
    OPENCODE = "opencode"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class PluginFormatDetection:
    source_format: DetectedPluginFormat
    marker_paths: tuple[Path, ...]
    unsupported_schema: str | None = None


_UNIVERSAL_RUNTIME_STATE_NAMES = frozenset({".git"})

_RUNTIME_STATE_NAMES: Mapping[DetectedPluginFormat, frozenset[str]] = MappingProxyType({
    DetectedPluginFormat.CLAUDE_CODE: frozenset({".in_use"})
})


def plugin_runtime_state_names(source_format: DetectedPluginFormat) -> frozenset[str]:
    return _UNIVERSAL_RUNTIME_STATE_NAMES | _RUNTIME_STATE_NAMES.get(
        source_format, frozenset()
    )


@dataclass(frozen=True, slots=True)
class PluginToolOverride:
    name: str | None = None
    exposure: Literal["programmatic", "direct", "direct_and_programmatic"] | None = None


@dataclass(frozen=True, slots=True)
class AdaptedMCPServer:
    source_id: str
    server: MCPServer
    config_file: Path


@dataclass(frozen=True, slots=True)
class AdaptedSkill:
    source_name: str
    description: str
    prompt: str
    source_path: Path
    allowed_tools: tuple[str, ...] = ()
    user_invocable: bool = True
    license: str | None = None
    compatibility: str | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    translation: Literal["synthetic_skill"] | None = None


@dataclass(frozen=True, slots=True)
class AdaptedHook:
    config: HookConfig
    source_path: Path
    protocol: HookProtocol


@dataclass(frozen=True, slots=True)
class PluginAdapterDiagnostic:
    severity: Literal["info", "warning", "error"]
    code: str
    path: Path
    message: str
    fatal: bool
    component: str


@dataclass(frozen=True, slots=True)
class AdaptedUnsupportedComponent:
    kind: str
    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class AdaptedPluginPackage:
    source_format: DetectedPluginFormat
    manifest_path: Path
    name: str
    version: str | None
    description: str
    namespace: str
    data_root: Path
    scope: SkillScope
    skill_roots: tuple[Path, ...]
    mcp_servers: tuple[AdaptedMCPServer, ...]
    tool_overrides: Mapping[str, PluginToolOverride]
    private_metadata: Mapping[str, object]
    author: str | None = None
    adapted_skills: tuple[AdaptedSkill, ...] = ()
    adapted_hooks: tuple[AdaptedHook, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginAdapterResult:
    package: AdaptedPluginPackage | None
    diagnostics: tuple[PluginAdapterDiagnostic, ...] = ()
    unsupported_components: tuple[AdaptedUnsupportedComponent, ...] = ()


class PluginFormatAdapter(Protocol):
    source_format: DetectedPluginFormat

    def adapt(
        self, *, root: Path, data_root_base: Path, scope: SkillScope
    ) -> PluginAdapterResult: ...


def detect_plugin_source_format(root: Path) -> PluginFormatDetection:
    native_manifest = root / "plugin.json"
    native_schema = _read_schema_id(native_manifest)
    if native_schema == _NATIVE_SCHEMA:
        return PluginFormatDetection(
            source_format=DetectedPluginFormat.AGENT_PLUGINS_1_0,
            marker_paths=(native_manifest,),
        )
    if native_schema is not None and native_schema.startswith(
        _AGENT_PLUGIN_SCHEMA_PREFIX
    ):
        return PluginFormatDetection(
            source_format=DetectedPluginFormat.AGENT_PLUGINS_1_0,
            marker_paths=(native_manifest,),
            unsupported_schema=native_schema,
        )

    markers: list[tuple[DetectedPluginFormat, Path]] = []
    for source_format, marker in (
        (DetectedPluginFormat.CODEX, root / ".codex-plugin" / "plugin.json"),
        (DetectedPluginFormat.CLAUDE_CODE, root / ".claude-plugin" / "plugin.json"),
        (DetectedPluginFormat.KIMI_CODE, root / "kimi.plugin.json"),
        (DetectedPluginFormat.KIMI_CODE, root / ".kimi-plugin" / "plugin.json"),
    ):
        if marker.is_file():
            markers.append((source_format, marker))
    markers.extend(
        (DetectedPluginFormat.OPENCODE, marker) for marker in _opencode_markers(root)
    )

    formats = {source_format for source_format, _ in markers}
    if len(formats) > 1:
        return PluginFormatDetection(
            source_format=DetectedPluginFormat.AMBIGUOUS,
            marker_paths=tuple(marker for _, marker in markers),
        )
    if markers:
        return PluginFormatDetection(
            source_format=markers[0][0],
            marker_paths=tuple(marker for _, marker in markers),
        )
    return PluginFormatDetection(
        source_format=DetectedPluginFormat.UNKNOWN,
        marker_paths=(native_manifest,) if native_manifest.exists() else (),
    )


def author_display_name(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        return author_display_name(value.get("name"))
    return None


def typescript_identifier(value: str) -> str:
    normalized = "".join(
        character
        if character.isascii() and (character.isalnum() or character in {"_", "$"})
        else "_"
        for character in value
    )
    if not normalized:
        return "_"
    if normalized[0].isalpha() or normalized[0] in {"_", "$"}:
        return normalized
    return f"_{normalized}"


def portable_skill_name(value: str, *, prefix: str = "") -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    normalized = f"{prefix}{normalized}" if normalized else prefix.rstrip("-")
    if not normalized:
        raise ValueError("skill name must contain an ASCII letter or digit")
    if len(normalized) <= _MAX_SKILL_NAME_LENGTH:
        return normalized
    digest = hashlib.blake2s(normalized.encode(), digest_size=5).hexdigest()
    prefix_length = _MAX_SKILL_NAME_LENGTH - len(digest) - 1
    return f"{normalized[:prefix_length].rstrip('-')}-{digest}"


def resolve_declared_path(root: Path, value: str) -> Path:
    if not value:
        raise ValueError("declared path cannot be empty")
    if "\\" in value or re.match(r"^[A-Za-z]:/", value):
        raise ValueError("declared path must use portable forward slashes")
    relative = PurePosixPath(value.removeprefix("./"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("declared path must stay inside the plugin root")
    candidate = root.joinpath(*relative.parts)
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("declared path must resolve inside the plugin root")
    return resolved


def relative_plugin_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return "<outside-plugin>"
    value = relative.as_posix()
    return value if value else "."


def _read_schema_id(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(read_safe(path, raise_on_error=True).text)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    schema = value.get("$schema")
    return schema if isinstance(schema, str) else None


def _opencode_markers(root: Path) -> tuple[Path, ...]:
    markers: list[Path] = []
    for directory in (root / ".opencode" / "plugins", root / ".opencode" / "tools"):
        if not directory.is_dir():
            continue
        markers.extend(
            path
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix.lower() in _OPENCODE_CODE_SUFFIXES
        )
    package_path = root / "package.json"
    package_marker = _opencode_package_marker(root, package_path)
    if package_marker is not None and package_marker not in markers:
        markers.append(package_marker)
    for path in (
        root / "opencode.json",
        root / "opencode.jsonc",
        root / ".opencode" / "opencode.json",
        root / ".opencode" / "opencode.jsonc",
    ):
        if path.is_file() and path not in markers:
            markers.append(path)
    skill_dir = root / ".opencode" / "skills"
    if skill_dir.is_dir():
        markers.extend(
            path
            for path in sorted(skill_dir.glob("*/SKILL.md"))
            if path.is_file() and path not in markers
        )
    return tuple(markers)


def _opencode_package_marker(root: Path, path: Path) -> Path | None:
    if not path.is_file():
        return None
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("package.json must resolve inside the plugin root")
        value = json.loads(read_safe(resolved, raise_on_error=True).text)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    keywords = value.get("keywords")
    is_opencode = isinstance(keywords, list) and any(
        keyword in {"opencode", "opencode-plugin"}
        for keyword in keywords
        if isinstance(keyword, str)
    )
    if not is_opencode:
        return None

    module = value.get("module")
    if isinstance(module, str):
        try:
            module_path = resolve_declared_path(root, module)
        except (OSError, ValueError):
            module_path = None
        if module_path is not None and module_path.is_file():
            return module_path
    return path
