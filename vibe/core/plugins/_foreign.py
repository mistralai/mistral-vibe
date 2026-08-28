from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Literal

from pydantic import ValidationError

from vibe.core.config import MCPHttp, MCPStaticAuth, MCPStdio, MCPStreamableHttp
from vibe.core.config.mcp_servers import MCPServerAddError, normalize_mcp_server_url
from vibe.core.plugins._compatibility import (
    AdaptedMCPServer,
    AdaptedPluginPackage,
    AdaptedSkill,
    AdaptedUnsupportedComponent,
    DetectedPluginFormat,
    PluginAdapterDiagnostic,
    PluginAdapterResult,
    author_display_name,
    portable_skill_name,
    resolve_declared_path,
    typescript_identifier,
)
from vibe.core.skills.models import SkillMetadata, SkillScope
from vibe.core.skills.parser import SkillParseError, parse_skill_markdown
from vibe.utils.io import read_safe

_ARGUMENT_PATTERN = re.compile(r"(?<!\\)\$(?:ARGUMENTS(?:\[\d+\])?|\d+)\b")
_DYNAMIC_COMMAND_PATTERN = re.compile(r"(?:^|\s)!`[^`]+`", re.MULTILINE)
_OPENCODE_CODE_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
_OPENCODE_ENV_REFERENCE = re.compile(r"\{env:[A-Za-z_][A-Za-z0-9_]*\}")


class OpenCodePluginAdapter:
    source_format = DetectedPluginFormat.OPENCODE

    def adapt(
        self, *, root: Path, data_root_base: Path, scope: SkillScope
    ) -> PluginAdapterResult:
        diagnostics: list[PluginAdapterDiagnostic] = []
        unsupported: list[AdaptedUnsupportedComponent] = []
        package_path, package = _opencode_package(root, diagnostics)
        name = _opencode_plugin_name(package.get("name"), root.name)
        namespace = typescript_identifier(name)
        data_root = data_root_base / namespace
        executable_paths = _opencode_executable_paths(root, package, diagnostics)
        custom_tool_paths = _opencode_custom_tool_paths(root, diagnostics)
        skills = _opencode_skills(root, diagnostics)
        mcp_servers, config_paths = _opencode_mcp_servers(
            root, data_root, diagnostics, unsupported
        )
        _report_opencode_executables(
            executable_paths,
            has_portable_capabilities=bool(skills or mcp_servers),
            diagnostics=diagnostics,
            unsupported=unsupported,
        )
        _report_opencode_custom_tools(
            custom_tool_paths,
            has_portable_capabilities=bool(skills or mcp_servers),
            diagnostics=diagnostics,
            unsupported=unsupported,
        )
        if not skills and not mcp_servers:
            if not executable_paths and not custom_tool_paths:
                diagnostics.append(
                    _diagnostic(
                        "opencode",
                        "no_portable_capabilities",
                        package_path or root,
                        "The OpenCode package contains no portable Agent Skills or declarative MCP servers.",
                        component="package",
                        severity="error",
                        fatal=True,
                    )
                )
            return PluginAdapterResult(
                package=None,
                diagnostics=tuple(diagnostics),
                unsupported_components=tuple(unsupported),
            )

        manifest_path = _opencode_manifest_path(
            package_path, config_paths, (*executable_paths, *custom_tool_paths), skills
        )
        description = _opencode_description(package.get("description"), name)
        version = package.get("version")
        return PluginAdapterResult(
            package=AdaptedPluginPackage(
                source_format=self.source_format,
                manifest_path=manifest_path,
                name=name,
                version=version if isinstance(version, str) else None,
                description=description,
                namespace=namespace,
                data_root=data_root,
                scope=scope,
                skill_roots=(),
                mcp_servers=mcp_servers,
                tool_overrides=MappingProxyType({}),
                private_metadata=MappingProxyType({
                    "package": dict(package),
                    "configPaths": tuple(str(path) for path in config_paths),
                }),
                author=author_display_name(package.get("author")),
                adapted_skills=skills,
            ),
            diagnostics=tuple(diagnostics),
            unsupported_components=tuple(unsupported),
        )


def _opencode_package(
    root: Path, diagnostics: list[PluginAdapterDiagnostic]
) -> tuple[Path | None, Mapping[str, object]]:
    path = root / "package.json"
    if not path.is_file():
        return None, {}
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("package.json must resolve inside the plugin root")
        value = json.loads(read_safe(resolved, raise_on_error=True).text)
        if not isinstance(value, Mapping):
            raise ValueError("package.json must contain an object")
        return resolved, value
    except (OSError, ValueError, json.JSONDecodeError) as error:
        diagnostics.append(
            _diagnostic(
                "opencode",
                "package_invalid",
                path,
                f"Failed to read OpenCode package metadata: {error}",
                component="package",
                severity="warning",
            )
        )
        return None, {}


def _opencode_plugin_name(value: object, fallback: str) -> str:
    raw = value if isinstance(value, str) and value.strip() else fallback
    return portable_skill_name(raw.removeprefix("@").replace("/", "-"))


def _opencode_description(value: object, name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"Portable capabilities imported from OpenCode package {name}."


def _opencode_manifest_path(
    package_path: Path | None,
    config_paths: Sequence[Path],
    executable_paths: Sequence[Path],
    skills: Sequence[AdaptedSkill],
) -> Path:
    if package_path is not None:
        return package_path
    if config_paths:
        return config_paths[0]
    if executable_paths:
        return executable_paths[0]
    return skills[0].source_path


def _opencode_executable_paths(
    root: Path,
    package: Mapping[str, object],
    diagnostics: list[PluginAdapterDiagnostic],
) -> tuple[Path, ...]:
    paths = set(
        _opencode_contained_code_paths(
            root,
            (root / ".opencode" / "plugins", root / "plugins"),
            diagnostics,
            component="executable_plugin",
        )
    )
    for value in _opencode_package_entrypoints(package):
        try:
            path = resolve_declared_path(root, value)
        except (OSError, ValueError):
            continue
        if path.is_file() and _is_opencode_code_path(path):
            paths.add(path)
    return tuple(sorted(paths))


def _opencode_custom_tool_paths(
    root: Path, diagnostics: list[PluginAdapterDiagnostic]
) -> tuple[Path, ...]:
    return _opencode_contained_code_paths(
        root, (root / ".opencode" / "tools",), diagnostics, component="custom_tool"
    )


def _opencode_contained_code_paths(
    root: Path,
    directories: Sequence[Path],
    diagnostics: list[PluginAdapterDiagnostic],
    *,
    component: str,
) -> tuple[Path, ...]:
    paths: set[Path] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or not _is_opencode_code_path(path):
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError as error:
                diagnostics.append(
                    _diagnostic(
                        "opencode",
                        "component_unreadable",
                        path,
                        f"Failed to resolve OpenCode {component}: {error}",
                        component=component,
                        severity="error",
                    )
                )
                continue
            if not resolved.is_relative_to(root):
                diagnostics.append(
                    _diagnostic(
                        "opencode",
                        "path_outside_root",
                        path,
                        f"OpenCode {component} must resolve inside the plugin root.",
                        component=component,
                        severity="error",
                    )
                )
                continue
            paths.add(resolved)
    return tuple(sorted(paths))


def _is_opencode_code_path(path: Path) -> bool:
    lowered = path.name.lower()
    if lowered.endswith((".d.ts", ".d.mts", ".d.cts")):
        return False
    return path.suffix.lower() in _OPENCODE_CODE_SUFFIXES


def _opencode_package_entrypoints(package: Mapping[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for field in ("main", "module", "browser", "exports"):
        _opencode_collect_strings(package.get(field), values)
    return tuple(values)


def _opencode_collect_strings(value: object, output: list[str]) -> None:
    if isinstance(value, str):
        output.append(value)
        return
    if isinstance(value, Mapping):
        for child in value.values():
            _opencode_collect_strings(child, output)
        return
    if isinstance(value, list):
        for child in value:
            _opencode_collect_strings(child, output)


def _report_opencode_executables(
    paths: Sequence[Path],
    *,
    has_portable_capabilities: bool,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> None:
    unsupported.extend(
        AdaptedUnsupportedComponent(
            kind="executable_plugin",
            path=path,
            reason="foreign_code_execution_unsupported",
        )
        for path in paths
    )
    if not paths:
        return
    diagnostics.append(
        _diagnostic(
            "opencode",
            "executable_format_unsupported",
            paths[0],
            "OpenCode JavaScript and TypeScript modules are retained as package data but are never imported or evaluated.",
            component="executable_plugin",
            severity="warning" if has_portable_capabilities else "error",
            fatal=not has_portable_capabilities,
        )
    )


def _report_opencode_custom_tools(
    paths: Sequence[Path],
    *,
    has_portable_capabilities: bool,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> None:
    unsupported.extend(
        AdaptedUnsupportedComponent(
            kind="custom_tool", path=path, reason="opencode_executable_tool_unsupported"
        )
        for path in paths
    )
    if not paths:
        return
    diagnostics.append(
        _diagnostic(
            "opencode",
            "custom_tools_unsupported",
            paths[0],
            "OpenCode executable custom tools are retained as package data but are never imported or evaluated.",
            component="custom_tool",
            severity="warning" if has_portable_capabilities else "error",
            fatal=not has_portable_capabilities,
        )
    )


def _opencode_skills(
    root: Path, diagnostics: list[PluginAdapterDiagnostic]
) -> tuple[AdaptedSkill, ...]:
    files: set[Path] = set()
    for directory in (
        root / "skills",
        root / ".opencode" / "skills",
        root / ".agents" / "skills",
        root / ".claude" / "skills",
    ):
        if not directory.is_dir():
            continue
        for path in directory.glob("*/SKILL.md"):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError as error:
                diagnostics.append(
                    _diagnostic(
                        "opencode",
                        "skill_unreadable",
                        path,
                        f"Failed to resolve OpenCode skill: {error}",
                        component="skill",
                        severity="error",
                    )
                )
                continue
            if not resolved.is_relative_to(root):
                diagnostics.append(
                    _diagnostic(
                        "opencode",
                        "path_outside_root",
                        path,
                        "OpenCode skill must resolve inside the plugin root.",
                        component="skill",
                        severity="error",
                    )
                )
                continue
            files.add(resolved)
    skills: list[AdaptedSkill] = []
    for path in sorted(files):
        skill = adapt_skill_file(
            path=path,
            source_format="opencode",
            diagnostics=diagnostics,
            normalize_vendor_fields=False,
        )
        if skill is None:
            continue
        if skill.source_name != path.parent.name:
            diagnostics.append(
                _diagnostic(
                    "opencode",
                    "skill_directory_mismatch",
                    path,
                    f"OpenCode skill name {skill.source_name!r} must match directory {path.parent.name!r}.",
                    component="skill",
                    severity="error",
                )
            )
            continue
        skills.append(skill)
        diagnostics.append(
            _diagnostic(
                "opencode",
                "skill_imported",
                path,
                f"Imported portable Agent Skill {skill.source_name!r} without evaluating OpenCode code.",
                component="skill",
                severity="info",
            )
        )
    return tuple(skills)


def _opencode_mcp_servers(
    root: Path,
    data_root: Path,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> tuple[tuple[AdaptedMCPServer, ...], tuple[Path, ...]]:
    servers: list[AdaptedMCPServer] = []
    config_paths = _opencode_config_paths(root, diagnostics)
    for path in config_paths:
        try:
            value = _opencode_load_jsonc(path)
            _report_opencode_config_extensions(path, value, diagnostics, unsupported)
            raw_servers = value.get("mcp")
            if raw_servers is None:
                continue
            if not isinstance(raw_servers, Mapping):
                raise ValueError("OpenCode mcp must be an object")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            diagnostics.append(
                _diagnostic(
                    "opencode",
                    "config_invalid",
                    path,
                    f"Failed to read declarative OpenCode config: {error}",
                    component="mcp",
                    severity="error",
                )
            )
            continue
        translated = _opencode_translate_mcp_mapping(
            path, raw_servers, diagnostics, unsupported
        )
        servers.extend(
            adapt_mcp_servers(
                root=root,
                data_root=data_root,
                raw_servers=translated,
                config_path=path,
                source_format="opencode",
                diagnostics=diagnostics,
                placeholders={},
            )
        )
    return tuple(servers), config_paths


def _opencode_config_paths(
    root: Path, diagnostics: list[PluginAdapterDiagnostic]
) -> tuple[Path, ...]:
    paths: list[Path] = []
    for directory in (root, root / ".opencode"):
        for name in ("opencode.json", "opencode.jsonc"):
            path = directory / name
            if not path.is_file():
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError as error:
                diagnostics.append(
                    _diagnostic(
                        "opencode",
                        "config_unreadable",
                        path,
                        f"Failed to resolve OpenCode config: {error}",
                        component="config",
                        severity="error",
                    )
                )
                continue
            if not resolved.is_relative_to(root):
                diagnostics.append(
                    _diagnostic(
                        "opencode",
                        "path_outside_root",
                        path,
                        "OpenCode config must resolve inside the plugin root.",
                        component="config",
                        severity="error",
                    )
                )
                continue
            paths.append(resolved)
    return tuple(paths)


def _opencode_load_jsonc(path: Path) -> Mapping[str, object]:
    text = read_safe(path, raise_on_error=True).text
    value = json.loads(
        _opencode_remove_trailing_commas(_opencode_remove_json_comments(text))
    )
    if not isinstance(value, Mapping):
        raise ValueError("OpenCode config must contain an object")
    return value


def _opencode_remove_json_comments(value: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(value):
        character = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == "/" and following == "/":
            index = _opencode_skip_line_comment(value, index + 2)
            continue
        if character == "/" and following == "*":
            index = _opencode_skip_block_comment(value, index + 2, output)
            continue
        output.append(character)
        index += 1
    return "".join(output)


def _opencode_skip_line_comment(value: str, index: int) -> int:
    while index < len(value) and value[index] not in "\r\n":
        index += 1
    return index


def _opencode_skip_block_comment(value: str, index: int, output: list[str]) -> int:
    while index + 1 < len(value):
        if value[index : index + 2] == "*/":
            return index + 2
        if value[index] in "\r\n":
            output.append(value[index])
        index += 1
    raise ValueError("unterminated JSONC block comment")


def _report_opencode_config_extensions(
    path: Path,
    value: Mapping[str, object],
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> None:
    fields = {
        "agent": ("agent", "opencode_agent_translation_unsupported"),
        "command": ("command", "opencode_command_translation_unsupported"),
        "plugin": ("plugin_dependency", "opencode_plugin_dependency_unsupported"),
        "provider": ("provider_extension", "opencode_provider_extension_unsupported"),
    }
    for field, (kind, reason) in fields.items():
        raw = value.get(field)
        if raw in (None, False, (), [], {}):
            continue
        unsupported.append(
            AdaptedUnsupportedComponent(kind=kind, path=path, reason=reason)
        )
        diagnostics.append(
            _diagnostic(
                "opencode",
                f"{field}_unsupported",
                path,
                f"OpenCode config field {field!r} has no safe declarative Unified Harness conversion and was skipped.",
                component=kind,
                severity="warning",
            )
        )


def _opencode_remove_trailing_commas(value: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(value):
        character = value[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == "," and _opencode_followed_by_closer(value, index + 1):
            index += 1
            continue
        output.append(character)
        index += 1
    return "".join(output)


def _opencode_followed_by_closer(value: str, index: int) -> bool:
    while index < len(value) and value[index].isspace():
        index += 1
    return index < len(value) and value[index] in "}]"


def _opencode_translate_mcp_mapping(
    path: Path,
    raw_servers: Mapping[object, object],
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> dict[str, object]:
    translated: dict[str, object] = {}
    for raw_name, raw_server in sorted(
        raw_servers.items(), key=lambda item: str(item[0])
    ):
        name = str(raw_name)
        server = _opencode_translate_mcp_server(
            path, name, raw_server, diagnostics, unsupported
        )
        if server is not None:
            translated[name] = server
    return translated


def _opencode_translate_mcp_server(
    path: Path,
    name: str,
    raw: object,
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> Mapping[str, object] | None:
    if not isinstance(raw, Mapping):
        _opencode_invalid_mcp(diagnostics, path, name, "must be an object")
        return None
    if raw.get("enabled") is False:
        diagnostics.append(
            _diagnostic(
                "opencode",
                "mcp_server_skipped",
                path,
                f"Skipped disabled OpenCode MCP server {name!r}.",
                component="mcp_server",
                severity="info",
            )
        )
        return None
    server_type = raw.get("type")
    if server_type == "local":
        translated = _opencode_translate_local_mcp(
            path, name, raw, diagnostics, unsupported
        )
    elif server_type == "remote":
        translated = _opencode_translate_remote_mcp(
            path, name, raw, diagnostics, unsupported
        )
    else:
        _opencode_invalid_mcp(
            diagnostics, path, name, f"has unsupported type {server_type!r}"
        )
        return None
    if translated is not None:
        diagnostics.append(
            _diagnostic(
                "opencode",
                "mcp_server_translated",
                path,
                f"Translated declarative OpenCode MCP server {name!r} without evaluating plugin code.",
                component="mcp_server",
                severity="info",
            )
        )
    return translated


def _opencode_translate_local_mcp(
    path: Path,
    name: str,
    raw: Mapping[object, object],
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> Mapping[str, object] | None:
    command = raw.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(value, str) for value in command)
    ):
        _opencode_invalid_mcp(
            diagnostics, path, name, "requires a non-empty string command array"
        )
        return None
    environment = raw.get("environment", {})
    try:
        env = _string_mapping(environment)
    except ValueError:
        _opencode_invalid_mcp(
            diagnostics, path, name, "environment must contain string values"
        )
        return None
    command_values = [value for value in command if isinstance(value, str)]
    if _opencode_has_env_reference([*command_values, *env.values()]):
        _opencode_unsupported_env_reference(diagnostics, unsupported, path, name)
        return None
    translated: dict[str, object] = {
        "command": command_values[0],
        "args": command_values[1:],
        "env": env,
    }
    if isinstance(raw.get("cwd"), str):
        translated["cwd"] = raw["cwd"]
    return translated


def _opencode_translate_remote_mcp(
    path: Path,
    name: str,
    raw: Mapping[object, object],
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
) -> Mapping[str, object] | None:
    oauth = raw.get("oauth")
    if oauth is not None and oauth is not False:
        diagnostics.append(
            _diagnostic(
                "opencode",
                "mcp_oauth_unsupported",
                path,
                f"OpenCode MCP server {name!r} requires host-managed OAuth and was skipped.",
                component="mcp_server",
                severity="warning",
            )
        )
        unsupported.append(
            AdaptedUnsupportedComponent(
                kind="mcp_oauth",
                path=path,
                reason="opencode_oauth_translation_unsupported",
            )
        )
        return None
    url = raw.get("url")
    try:
        headers = _string_mapping(raw.get("headers"))
    except ValueError:
        _opencode_invalid_mcp(
            diagnostics, path, name, "headers must contain string values"
        )
        return None
    if not isinstance(url, str) or not url:
        _opencode_invalid_mcp(diagnostics, path, name, "requires a non-empty url")
        return None
    if _opencode_has_env_reference([url, *headers.values()]):
        _opencode_unsupported_env_reference(diagnostics, unsupported, path, name)
        return None
    return {"url": url, "headers": headers, "type": "http"}


def _opencode_has_env_reference(values: Sequence[str]) -> bool:
    return any(_OPENCODE_ENV_REFERENCE.search(value) for value in values)


def _opencode_unsupported_env_reference(
    diagnostics: list[PluginAdapterDiagnostic],
    unsupported: list[AdaptedUnsupportedComponent],
    path: Path,
    name: str,
) -> None:
    diagnostics.append(
        _diagnostic(
            "opencode",
            "mcp_environment_reference_unsupported",
            path,
            f"OpenCode MCP server {name!r} uses runtime environment interpolation and was skipped.",
            component="mcp_server",
            severity="warning",
        )
    )
    unsupported.append(
        AdaptedUnsupportedComponent(
            kind="mcp_environment_reference",
            path=path,
            reason="opencode_environment_interpolation_unsupported",
        )
    )


def _opencode_invalid_mcp(
    diagnostics: list[PluginAdapterDiagnostic], path: Path, name: str, reason: str
) -> None:
    diagnostics.append(
        _diagnostic(
            "opencode",
            "mcp_server_invalid",
            path,
            f"OpenCode MCP server {name!r} {reason}.",
            component="mcp_server",
            severity="error",
        )
    )


def declared_paths(value: object) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)


def markdown_files_for_declared_path(root: Path, value: str) -> tuple[Path, ...]:
    resolved = resolve_declared_path(root, value)
    if resolved.is_file() and resolved.suffix.lower() == ".md":
        return (resolved,)
    if not resolved.is_dir():
        raise ValueError("declared path must resolve to a Markdown file or directory")
    return tuple(sorted(path for path in resolved.rglob("*.md") if path.is_file()))


def skill_files_for_declared_path(root: Path, value: str) -> tuple[Path, ...]:
    resolved = resolve_declared_path(root, value)
    if resolved.is_file() and resolved.name == "SKILL.md":
        return (resolved,)
    direct = resolved / "SKILL.md"
    if direct.is_file():
        return (direct.resolve(strict=True),)
    if not resolved.is_dir():
        raise ValueError("skills path must resolve to a skill directory")
    return tuple(
        sorted(
            path.resolve(strict=True)
            for path in resolved.glob("*/SKILL.md")
            if path.is_file()
        )
    )


def adapt_skill_file(
    *,
    path: Path,
    source_format: str,
    diagnostics: list[PluginAdapterDiagnostic],
    normalize_vendor_fields: bool,
) -> AdaptedSkill | None:
    try:
        frontmatter, body = parse_skill_markdown(
            read_safe(path, raise_on_error=True).text
        )
        normalized = dict(frontmatter)
        if normalize_vendor_fields:
            normalized = _normalize_vendor_skill_frontmatter(
                normalized,
                path=path,
                source_format=source_format,
                diagnostics=diagnostics,
            )
        elif "tools" in normalized and "allowed-tools" not in normalized:
            normalized["allowed-tools"] = _string_list(normalized["tools"])
            diagnostics.append(
                _diagnostic(
                    source_format,
                    "skill_tools_normalized",
                    path,
                    "Vendor skill tool guidance was normalized to Agent Skills allowed-tools metadata; Runtime approval policy remains authoritative.",
                    component="skill",
                    severity="info",
                )
            )
        metadata = SkillMetadata.model_validate(normalized)
    except (OSError, SkillParseError, ValidationError, ValueError) as error:
        diagnostics.append(
            _diagnostic(
                source_format,
                "skill_invalid",
                path,
                f"Failed to import skill: {_safe_error(error)}",
                component="skill",
                severity="error",
            )
        )
        return None

    prompt = _with_tool_guidance(body.strip(), metadata.allowed_tools)
    return AdaptedSkill(
        source_name=metadata.name,
        description=metadata.description,
        prompt=prompt,
        source_path=path,
        allowed_tools=tuple(metadata.allowed_tools),
        user_invocable=metadata.user_invocable,
        license=metadata.license,
        compatibility=metadata.compatibility,
        metadata=MappingProxyType(dict(metadata.metadata)),
    )


def adapt_static_command(
    *,
    path: Path,
    source_name: str,
    source_format: str,
    diagnostics: list[PluginAdapterDiagnostic],
) -> AdaptedSkill | None:
    try:
        frontmatter, body = parse_skill_markdown(
            read_safe(path, raise_on_error=True).text
        )
    except (OSError, SkillParseError) as error:
        diagnostics.append(
            _diagnostic(
                source_format,
                "command_invalid",
                path,
                f"Failed to import static command: {_safe_error(error)}",
                component="command",
                severity="error",
            )
        )
        return None

    body = body.strip()
    if not body:
        diagnostics.append(
            _diagnostic(
                source_format,
                "command_empty",
                path,
                "Static command has no prompt body and was not converted.",
                component="command",
                severity="warning",
            )
        )
        return None
    if _DYNAMIC_COMMAND_PATTERN.search(body):
        diagnostics.append(
            _diagnostic(
                source_format,
                "command_dynamic_execution_unsupported",
                path,
                "Static command uses dynamic shell context injection and was not executed or converted.",
                component="command",
                severity="warning",
            )
        )
        return None
    if _ARGUMENT_PATTERN.search(body):
        diagnostics.append(
            _diagnostic(
                source_format,
                "command_arguments_unsupported",
                path,
                "Static command argument substitution is not supported by the skill invocation contract, so the command was not converted.",
                component="command",
                severity="warning",
            )
        )
        return None

    raw_name = frontmatter.get("name")
    command_name = raw_name.strip() if isinstance(raw_name, str) else source_name
    generated_name = portable_skill_name(command_name, prefix="command-")
    raw_description = frontmatter.get("description")
    description = (
        raw_description.strip()
        if isinstance(raw_description, str) and raw_description.strip()
        else _description_from_body(body)
    )
    allowed_tools = _string_list(
        frontmatter.get("allowed-tools", frontmatter.get("tools"))
    )
    unsupported = sorted(
        key
        for key in frontmatter
        if key not in {"name", "description", "allowed-tools", "tools", "argument-hint"}
    )
    if unsupported:
        diagnostics.append(
            _diagnostic(
                source_format,
                "command_metadata_partially_supported",
                path,
                "Static command metadata fields are retained as source data but not applied by the Runtime: "
                + ", ".join(unsupported),
                component="command",
                severity="info",
            )
        )
    if "argument-hint" in frontmatter:
        diagnostics.append(
            _diagnostic(
                source_format,
                "command_argument_hint_ui_unsupported",
                path,
                "The source command argument hint is UI metadata and is not exposed by Vibe CLI.",
                component="command",
                severity="info",
            )
        )
    if allowed_tools:
        diagnostics.append(
            _diagnostic(
                source_format,
                "command_tool_policy_preserved_as_guidance",
                path,
                "The source command tool allowlist is preserved in the generated skill, but it does not bypass Runtime approvals.",
                component="command",
                severity="info",
            )
        )

    prompt = _with_tool_guidance(body, allowed_tools)
    return AdaptedSkill(
        source_name=generated_name,
        description=description[:1024],
        prompt=prompt,
        source_path=path,
        allowed_tools=tuple(allowed_tools),
        metadata=MappingProxyType({"source-command": command_name}),
        translation="synthetic_skill",
    )


def adapt_mcp_servers(
    *,
    root: Path,
    data_root: Path,
    raw_servers: Mapping[str, object],
    config_path: Path,
    source_format: str,
    diagnostics: list[PluginAdapterDiagnostic],
    placeholders: Mapping[str, str],
) -> tuple[AdaptedMCPServer, ...]:
    servers: list[AdaptedMCPServer] = []
    for source_id, raw in sorted(raw_servers.items()):
        try:
            server = _adapt_mcp_server(
                root=root,
                data_root=data_root,
                source_id=source_id,
                raw=raw,
                placeholders=placeholders,
            )
        except (MCPServerAddError, OSError, ValidationError, ValueError) as error:
            diagnostics.append(
                _diagnostic(
                    source_format,
                    "mcp_server_invalid",
                    config_path,
                    f"Failed to import MCP server {source_id!r}: {_safe_error(error)}",
                    component="mcp_server",
                    severity="error",
                )
            )
            continue
        servers.append(
            AdaptedMCPServer(
                source_id=source_id, server=server, config_file=config_path
            )
        )
    return tuple(servers)


def mcp_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    wrapped = value.get("mcpServers")
    if isinstance(wrapped, Mapping):
        return {str(key): item for key, item in wrapped.items()}
    return {str(key): item for key, item in value.items()}


def _adapt_mcp_server(
    *,
    root: Path,
    data_root: Path,
    source_id: str,
    raw: object,
    placeholders: Mapping[str, str],
) -> MCPHttp | MCPStreamableHttp | MCPStdio:
    if not isinstance(raw, Mapping):
        raise ValueError("MCP server definition must be an object")
    if command_value := raw.get("command"):
        if not isinstance(command_value, str):
            raise ValueError("stdio command must be a string")
        expanded_command = _expand(command_value, placeholders)
        command = _resolve_command(root, command_value, expanded_command)
        args = _string_list(raw.get("args"))
        env = _string_mapping(raw.get("env"))
        env = {name: _expand(value, placeholders) for name, value in env.items()}
        reserved = sorted({"PLUGIN_ROOT", "PLUGIN_DATA"} & env.keys())
        if reserved:
            raise ValueError(
                "env cannot define Runtime-reserved variables: " + ", ".join(reserved)
            )
        env.update({"PLUGIN_ROOT": str(root), "PLUGIN_DATA": str(data_root)})
        cwd_value = raw.get("cwd")
        cwd = _resolve_cwd(
            root,
            _expand(cwd_value, placeholders) if isinstance(cwd_value, str) else None,
        )
        return MCPStdio(
            name=source_id,
            transport="stdio",
            command=[command],
            args=[_expand(value, placeholders) for value in args],
            env=env,
            cwd=str(cwd),
        )

    url = raw.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("remote MCP server must define a non-empty url")
    headers = _string_mapping(raw.get("headers"))
    auth = MCPStaticAuth(
        headers={k: _expand(v, placeholders) for k, v in headers.items()}
    )
    transport = raw.get("type", raw.get("transport", "http"))
    normalized_url = normalize_mcp_server_url(_expand(url, placeholders))
    if transport == "streamable-http":
        return MCPStreamableHttp(
            name=source_id, transport="streamable-http", url=normalized_url, auth=auth
        )
    return MCPHttp(name=source_id, transport="http", url=normalized_url, auth=auth)


def _normalize_vendor_skill_frontmatter(
    raw: dict[str, object],
    *,
    path: Path,
    source_format: str,
    diagnostics: list[PluginAdapterDiagnostic],
) -> dict[str, object]:
    skill_type = raw.get("type")
    if skill_type not in {None, "prompt"}:
        raise ValueError(f"unsupported vendor skill type {skill_type!r}")
    if raw.get("disableModelInvocation") is True:
        diagnostics.append(
            _diagnostic(
                source_format,
                "skill_model_invocation_constraint_unsupported",
                path,
                "The source skill disables model invocation, which the current Core skill catalog cannot represent; the skill was not imported.",
                component="skill",
                severity="warning",
            )
        )
        raise ValueError("disableModelInvocation=true is not portable")

    normalized = {
        key: value
        for key, value in raw.items()
        if key
        in {
            "name",
            "description",
            "license",
            "compatibility",
            "metadata",
            "allowed-tools",
            "user-invocable",
        }
    }
    if "allowed-tools" not in normalized and "tools" in raw:
        normalized["allowed-tools"] = raw["tools"]
    vendor_metadata = {
        key: value
        for key, value in raw.items()
        if key in {"type", "whenToUse", "disableModelInvocation"}
    }
    if vendor_metadata:
        existing_metadata = normalized.get("metadata")
        metadata = (
            dict(existing_metadata) if isinstance(existing_metadata, Mapping) else {}
        )
        metadata.update({
            f"vendor.{key}": json.dumps(value) for key, value in vendor_metadata.items()
        })
        normalized["metadata"] = metadata
        diagnostics.append(
            _diagnostic(
                source_format,
                "skill_vendor_metadata_normalized",
                path,
                "Kimi-specific skill frontmatter was retained as metadata and normalized to the portable Agent Skills fields.",
                component="skill",
                severity="info",
            )
        )
    return normalized


def _with_tool_guidance(body: str, allowed_tools: Sequence[str]) -> str:
    if not allowed_tools:
        return body
    tools = ", ".join(allowed_tools)
    return (
        "## Imported tool constraints\n\n"
        f"The source capability declared these tools: {tools}. Treat this as workflow guidance; Runtime permissions and approvals remain authoritative.\n\n"
        f"{body}"
    )


def _description_from_body(body: str) -> str:
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    return (first or "Imported static command.")[:1024]


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[\s,]+", value) if part]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError("expected a string or array of strings")


def _string_mapping(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("expected an object with string values")
    return dict(value)


def _expand(value: str, placeholders: Mapping[str, str]) -> str:
    for placeholder, replacement in placeholders.items():
        value = value.replace(placeholder, replacement)
    return value


def _resolve_command(root: Path, original: str, expanded: str) -> str:
    if original.startswith("./"):
        candidate = root / original[2:]
    elif original.startswith("${"):
        candidate = Path(expanded)
    else:
        if "/" in original or "\\" in original or original in {".", ".."}:
            raise ValueError(
                "stdio command must be a bare executable or a contained path"
            )
        return original
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("stdio command must resolve inside the plugin root")
    return str(resolved)


def _resolve_cwd(root: Path, value: str | None) -> Path:
    if value is None or value in {".", "./"}:
        return root
    candidate = (
        Path(value)
        if Path(value).is_absolute()
        else root / PurePosixPath(value.removeprefix("./"))
    )
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("stdio cwd must resolve inside the plugin root")
    return resolved


def _diagnostic(
    source_format: str,
    suffix: str,
    path: Path,
    message: str,
    *,
    component: str,
    severity: Literal["info", "warning", "error"],
    fatal: bool = False,
) -> PluginAdapterDiagnostic:
    return PluginAdapterDiagnostic(
        severity=severity,
        code=f"plugin.compatibility.{source_format}.{suffix}",
        path=path,
        message=message,
        fatal=fatal,
        component=component,
    )


def _safe_error(error: BaseException) -> str:
    if not isinstance(error, ValidationError):
        return str(error)
    return "; ".join(
        f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
        for issue in error.errors(include_input=False, include_url=False)
    )
