from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from vibe.core.config import MCPHttp, MCPOAuth, MCPStaticAuth, MCPStdio
from vibe.core.config.mcp_servers import MCPServerAddError, normalize_mcp_server_url
from vibe.core.plugins._compatibility import (
    AdaptedMCPServer,
    AdaptedPluginPackage,
    AdaptedUnsupportedComponent,
    DetectedPluginFormat,
    PluginAdapterDiagnostic,
    PluginAdapterResult,
    author_display_name,
    resolve_declared_path,
    typescript_identifier,
)
from vibe.core.skills.models import SkillScope
from vibe.utils.io import read_safe


class _CodexPluginManifest(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=True)

    name: str | None = Field(default=None, max_length=256)
    version: str | None = None
    description: str | None = None
    author: JsonValue = None
    skills: JsonValue = None
    mcp_servers: JsonValue = Field(default=None, alias="mcpServers")
    apps: JsonValue = None
    hooks: JsonValue = None
    interface: JsonValue = None


class _CodexMCPConfiguration(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=True)

    mcp_servers: dict[str, object] = Field(alias="mcpServers")


class _CodexStdioServer(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    type: Literal["stdio"] | None = None
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


class _CodexHTTPServer(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    type: Literal["http"]
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    bearer_token_env_var: str | None = None
    oauth_resource: str | None = None


class CodexPluginAdapter:
    source_format = DetectedPluginFormat.CODEX

    def adapt(
        self, *, root: Path, data_root_base: Path, scope: SkillScope
    ) -> PluginAdapterResult:
        manifest_path = root / ".codex-plugin" / "plugin.json"
        try:
            resolved_manifest = resolve_declared_path(root, ".codex-plugin/plugin.json")
            if not resolved_manifest.is_file():
                raise ValueError("Codex plugin manifest must be a regular file")
            raw_manifest = json.loads(
                read_safe(resolved_manifest, raise_on_error=True).text
            )
            manifest = _CodexPluginManifest.model_validate(raw_manifest)
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
            return PluginAdapterResult(
                package=None,
                diagnostics=(
                    PluginAdapterDiagnostic(
                        severity="error",
                        code="plugin.compatibility.codex.manifest_invalid",
                        path=manifest_path,
                        message=f"Failed to load Codex manifest: {_safe_error(error)}",
                        fatal=True,
                        component="manifest",
                    ),
                ),
            )

        diagnostics: list[PluginAdapterDiagnostic] = []
        unsupported: list[AdaptedUnsupportedComponent] = []
        name = manifest.name if manifest.name and manifest.name.strip() else root.name
        version = manifest.version.strip() if manifest.version else None
        private_metadata: dict[str, object] = {"codexManifest": raw_manifest}
        namespace = typescript_identifier(name)
        data_root = data_root_base / namespace
        skill_roots = self._skill_roots(root, manifest, diagnostics)
        mcp_servers, mcp_metadata = self._mcp_servers(
            root, data_root, resolved_manifest, manifest, diagnostics
        )
        if mcp_metadata is not None:
            private_metadata["codexMcp"] = mcp_metadata

        self._report_interface(resolved_manifest, manifest, diagnostics, unsupported)
        self._report_apps(root, resolved_manifest, manifest, diagnostics, unsupported)
        self._report_hooks(root, resolved_manifest, manifest, diagnostics, unsupported)
        self._report_agent_metadata(root, skill_roots, diagnostics, unsupported)

        package = AdaptedPluginPackage(
            source_format=self.source_format,
            manifest_path=resolved_manifest,
            name=name,
            version=version or None,
            description=manifest.description or f"Capabilities provided by {name}.",
            namespace=namespace,
            data_root=data_root,
            scope=scope,
            skill_roots=skill_roots,
            mcp_servers=mcp_servers,
            tool_overrides=MappingProxyType({}),
            private_metadata=MappingProxyType(private_metadata),
            author=author_display_name(manifest.author),
        )
        return PluginAdapterResult(
            package=package,
            diagnostics=tuple(diagnostics),
            unsupported_components=tuple(unsupported),
        )

    @staticmethod
    def _skill_roots(
        root: Path,
        manifest: _CodexPluginManifest,
        diagnostics: list[PluginAdapterDiagnostic],
    ) -> tuple[Path, ...]:
        values = _declared_paths(manifest.skills)
        if values is None:
            diagnostics.append(
                PluginAdapterDiagnostic(
                    severity="error",
                    code="plugin.compatibility.codex.skills_declaration_invalid",
                    path=root / ".codex-plugin" / "plugin.json",
                    message=(
                        "Codex skills declaration must be a relative path string or "
                        "an array of relative path strings."
                    ),
                    fatal=False,
                    component="skill",
                )
            )
            values = ()
        implicit = root / "skills"
        if implicit.exists() or implicit.is_symlink():
            values = (*values, "./skills/")

        roots: set[Path] = set()
        for value in values:
            try:
                skills_root = resolve_declared_path(root, value)
                if not skills_root.is_dir():
                    raise ValueError("Codex skills path must resolve to a directory")
            except (OSError, ValueError) as error:
                diagnostics.append(
                    PluginAdapterDiagnostic(
                        severity="error",
                        code="plugin.path.outside_root",
                        path=root / PurePosixPath(value.removeprefix("./")),
                        message=f"Failed to load Codex skills: {error}",
                        fatal=False,
                        component="skill",
                    )
                )
                continue
            roots.add(skills_root)
        return tuple(sorted(roots))

    @staticmethod
    def _mcp_servers(
        root: Path,
        data_root: Path,
        manifest_path: Path,
        manifest: _CodexPluginManifest,
        diagnostics: list[PluginAdapterDiagnostic],
    ) -> tuple[tuple[AdaptedMCPServer, ...], object | None]:
        declared = manifest.mcp_servers
        implicit = root / ".mcp.json"
        sources: list[tuple[Path, Mapping[str, object], object]] = []
        if implicit.exists() or implicit.is_symlink():
            loaded = _load_mcp_config(root, "./.mcp.json", diagnostics)
            if loaded is not None:
                sources.append(loaded)

        if isinstance(declared, Mapping):
            sources.append((manifest_path, declared, declared))
        elif isinstance(declared, str):
            loaded = _load_mcp_config(root, declared, diagnostics)
            if loaded is not None and all(path != loaded[0] for path, _, _ in sources):
                sources.append(loaded)
        elif declared is not None:
            diagnostics.append(
                PluginAdapterDiagnostic(
                    severity="error",
                    code="plugin.compatibility.codex.mcp_declaration_invalid",
                    path=root / ".codex-plugin" / "plugin.json",
                    message=(
                        "Codex mcpServers declaration must be a relative path string "
                        "or an inline object."
                    ),
                    fatal=False,
                    component="mcp",
                )
            )

        loaded_servers = [
            server
            for config_path, raw_servers, _ in sources
            for server in _load_mcp_server_definitions(
                root=root,
                data_root=data_root,
                raw_servers=raw_servers,
                config_path=config_path,
                diagnostics=diagnostics,
            )
        ]
        servers = _remove_mcp_server_collisions(loaded_servers, diagnostics)
        metadata = {
            (
                config_path.relative_to(root).as_posix()
                if config_path.is_relative_to(root)
                else ".codex-plugin/plugin.json"
            ): raw
            for config_path, _, raw in sources
        }
        return servers, metadata or None

    @staticmethod
    def _report_interface(
        manifest_path: Path,
        manifest: _CodexPluginManifest,
        diagnostics: list[PluginAdapterDiagnostic],
        unsupported: list[AdaptedUnsupportedComponent],
    ) -> None:
        if manifest.interface is None:
            return
        if not isinstance(manifest.interface, dict):
            diagnostics.append(
                PluginAdapterDiagnostic(
                    severity="warning",
                    code="plugin.compatibility.codex.interface_metadata_invalid",
                    path=manifest_path,
                    message="Codex interface metadata must be an object.",
                    fatal=False,
                    component="interface",
                )
            )
        diagnostics.append(
            PluginAdapterDiagnostic(
                severity="info",
                code="plugin.compatibility.codex.interface_metadata_unsupported",
                path=manifest_path,
                message=(
                    "Codex interface and branding metadata is retained privately but "
                    "is not exposed as a Unified Harness capability."
                ),
                fatal=False,
                component="interface",
            )
        )
        unsupported.append(
            AdaptedUnsupportedComponent(
                kind="interface_metadata",
                path=manifest_path,
                reason="codex_interface_metadata_runtime_private",
            )
        )

    @staticmethod
    def _report_apps(
        root: Path,
        manifest_path: Path,
        manifest: _CodexPluginManifest,
        diagnostics: list[PluginAdapterDiagnostic],
        unsupported: list[AdaptedUnsupportedComponent],
    ) -> None:
        implicit = root / ".app.json"
        if manifest.apps is None and not (implicit.exists() or implicit.is_symlink()):
            return
        declared = manifest.apps
        if declared is not None and not isinstance(declared, str):
            path = manifest_path
            diagnostics.append(
                PluginAdapterDiagnostic(
                    severity="warning",
                    code="plugin.compatibility.codex.app_invalid",
                    path=path,
                    message="Codex Apps declaration must be a relative path string.",
                    fatal=False,
                    component="app",
                )
            )
        else:
            value = declared or "./.app.json"
            try:
                path = resolve_declared_path(root, value)
                if not path.is_file():
                    raise ValueError(
                        "Codex Apps declaration must resolve to a regular file"
                    )
            except (OSError, ValueError) as error:
                path = manifest_path
                diagnostics.append(
                    PluginAdapterDiagnostic(
                        severity="warning",
                        code="plugin.compatibility.codex.app_invalid",
                        path=path,
                        message=f"Codex Apps declaration cannot be read: {error}",
                        fatal=False,
                        component="app",
                    )
                )
        diagnostics.append(
            PluginAdapterDiagnostic(
                severity="warning",
                code="plugin.compatibility.codex.openai_app_unsupported",
                path=path,
                message=(
                    "OpenAI Apps and ChatGPT UI extensions are not available in the "
                    "headless Unified Harness Runtime."
                ),
                fatal=False,
                component="app",
            )
        )
        unsupported.append(
            AdaptedUnsupportedComponent(
                kind="openai_app", path=path, reason="openai_apps_ui_unsupported"
            )
        )

    @staticmethod
    def _report_hooks(
        root: Path,
        manifest_path: Path,
        manifest: _CodexPluginManifest,
        diagnostics: list[PluginAdapterDiagnostic],
        unsupported: list[AdaptedUnsupportedComponent],
    ) -> None:
        declared = manifest.hooks
        values = _declared_paths(declared)
        if values is None:
            if isinstance(declared, Mapping) or (
                isinstance(declared, list)
                and declared
                and all(isinstance(item, Mapping) for item in declared)
            ):
                diagnostics.append(
                    PluginAdapterDiagnostic(
                        severity="warning",
                        code="plugin.compatibility.codex.hooks_unsupported",
                        path=manifest_path,
                        message=(
                            "Inline Codex hooks are not executed by this compatibility "
                            "adapter. Valid independent skills and MCP servers remain active."
                        ),
                        fatal=False,
                        component="hook",
                    )
                )
                unsupported.append(
                    AdaptedUnsupportedComponent(
                        kind="codex_hooks",
                        path=manifest_path,
                        reason="codex_hook_semantics_unsupported",
                    )
                )
                return
            diagnostics.append(
                PluginAdapterDiagnostic(
                    severity="warning",
                    code="plugin.compatibility.codex.hooks_invalid",
                    path=manifest_path,
                    message=(
                        "Codex hooks declaration must be a relative path string, an "
                        "array of relative path strings, an object, or an array of objects."
                    ),
                    fatal=False,
                    component="hook",
                )
            )
            return
        if declared is None:
            hooks_path = root / "hooks.json"
            values = (
                ("hooks.json",)
                if hooks_path.exists() or hooks_path.is_symlink()
                else ()
            )

        for value in values:
            try:
                resolved = resolve_declared_path(root, value)
                if not resolved.is_file():
                    raise ValueError("Codex hooks must resolve to a regular file")
            except (OSError, ValueError) as error:
                diagnostics.append(
                    PluginAdapterDiagnostic(
                        severity="warning",
                        code="plugin.compatibility.codex.hooks_invalid",
                        path=manifest_path,
                        message=f"Codex hook configuration cannot be read: {error}",
                        fatal=False,
                        component="hook",
                    )
                )
                continue
            diagnostics.append(
                PluginAdapterDiagnostic(
                    severity="warning",
                    code="plugin.compatibility.codex.hooks_unsupported",
                    path=resolved,
                    message=(
                        "Codex hook semantics are not executed by this compatibility "
                        "adapter. Valid independent skills and MCP servers remain active."
                    ),
                    fatal=False,
                    component="hook",
                )
            )
            unsupported.append(
                AdaptedUnsupportedComponent(
                    kind="codex_hooks",
                    path=resolved,
                    reason="codex_hook_semantics_unsupported",
                )
            )

    @staticmethod
    def _report_agent_metadata(
        root: Path,
        skill_roots: tuple[Path, ...],
        diagnostics: list[PluginAdapterDiagnostic],
        unsupported: list[AdaptedUnsupportedComponent],
    ) -> None:
        candidates: set[Path] = set()
        for skill_root in skill_roots:
            try:
                skill_dirs = tuple(skill_root.iterdir())
            except OSError:
                continue
            for skill_dir in skill_dirs:
                for filename in ("openai.yaml", "openai.yml"):
                    candidate = skill_dir / "agents" / filename
                    if candidate.exists() or candidate.is_symlink():
                        candidates.add(candidate)
        agents_root = root / "agents"
        if agents_root.is_dir():
            try:
                candidates.update(
                    path for path in agents_root.iterdir() if path.is_file()
                )
            except OSError:
                pass

        for candidate in sorted(candidates):
            try:
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    raise ValueError(
                        "agent metadata must resolve inside the plugin root"
                    )
            except (OSError, ValueError) as error:
                diagnostics.append(
                    PluginAdapterDiagnostic(
                        severity="warning",
                        code="plugin.path.outside_root",
                        path=candidate,
                        message=f"Codex agent metadata cannot be read: {error}",
                        fatal=False,
                        component="agent_metadata",
                    )
                )
                continue
            diagnostics.append(
                PluginAdapterDiagnostic(
                    severity="info",
                    code="plugin.compatibility.codex.agent_metadata_unsupported",
                    path=resolved,
                    message=(
                        "Codex agent and per-skill presentation metadata is retained "
                        "as package data but is not converted into a Harness capability."
                    ),
                    fatal=False,
                    component="agent_metadata",
                )
            )
            unsupported.append(
                AdaptedUnsupportedComponent(
                    kind="agent_metadata",
                    path=resolved,
                    reason="codex_agent_metadata_unsupported",
                )
            )


def _declared_paths(value: JsonValue) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list):
        return None
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        paths.append(item)
    return tuple(paths)


def _load_mcp_config(
    root: Path, value: str, diagnostics: list[PluginAdapterDiagnostic]
) -> tuple[Path, Mapping[str, object], object] | None:
    config_path = root / PurePosixPath(value.removeprefix("./"))
    try:
        resolved_config = resolve_declared_path(root, value)
        if not resolved_config.is_file():
            raise ValueError("Codex MCP configuration must be a regular file")
        raw = json.loads(read_safe(resolved_config, raise_on_error=True).text)
        config = _CodexMCPConfiguration.model_validate(raw)
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
        diagnostics.append(
            PluginAdapterDiagnostic(
                severity="error",
                code="plugin.compatibility.codex.mcp_config_invalid",
                path=config_path,
                message=f"Failed to load Codex MCP configuration: {_safe_error(error)}",
                fatal=False,
                component="mcp",
            )
        )
        return None
    return resolved_config, config.mcp_servers, raw


def _load_mcp_server_definitions(
    *,
    root: Path,
    data_root: Path,
    raw_servers: Mapping[str, object],
    config_path: Path,
    diagnostics: list[PluginAdapterDiagnostic],
) -> tuple[AdaptedMCPServer, ...]:
    servers: list[AdaptedMCPServer] = []
    for source_id, raw_server in sorted(raw_servers.items()):
        try:
            server, server_diagnostics = _codex_mcp_server(
                root=root,
                data_root=data_root,
                source_id=source_id,
                raw=raw_server,
                config_path=config_path,
            )
        except (MCPServerAddError, OSError, ValueError, ValidationError) as error:
            diagnostics.append(
                PluginAdapterDiagnostic(
                    severity="error",
                    code="plugin.compatibility.codex.mcp_server_invalid",
                    path=config_path,
                    message=(
                        f"Failed to load Codex MCP server {source_id!r}: "
                        f"{_safe_error(error)}"
                    ),
                    fatal=False,
                    component="mcp_server",
                )
            )
            continue
        diagnostics.extend(server_diagnostics)
        servers.append(
            AdaptedMCPServer(
                source_id=source_id, server=server, config_file=config_path
            )
        )
    return tuple(servers)


def _remove_mcp_server_collisions(
    servers: list[AdaptedMCPServer], diagnostics: list[PluginAdapterDiagnostic]
) -> tuple[AdaptedMCPServer, ...]:
    grouped: dict[str, list[AdaptedMCPServer]] = {}
    for server in servers:
        grouped.setdefault(server.source_id, []).append(server)

    selected: list[AdaptedMCPServer] = []
    for source_id, matches in sorted(grouped.items()):
        if len(matches) == 1:
            selected.append(matches[0])
            continue
        diagnostics.append(
            PluginAdapterDiagnostic(
                severity="error",
                code="plugin.compatibility.codex.mcp_server_collision",
                path=matches[0].config_file,
                message=(
                    f"Codex MCP server {source_id!r} is declared by multiple "
                    "configuration sources; all conflicting declarations were disabled."
                ),
                fatal=False,
                component="mcp_server",
            )
        )
    return tuple(selected)


def _codex_mcp_server(
    *, root: Path, data_root: Path, source_id: str, raw: object, config_path: Path
) -> tuple[MCPHttp | MCPStdio, tuple[PluginAdapterDiagnostic, ...]]:
    if not isinstance(raw, Mapping):
        raise ValueError("MCP server definition must be an object")
    if "command" in raw:
        parsed = _CodexStdioServer.model_validate(raw)
        command = _resolve_command(root, parsed.command)
        cwd = _resolve_cwd(root, parsed.cwd)
        env = dict(parsed.env)
        reserved = sorted({"PLUGIN_ROOT", "PLUGIN_DATA"} & env.keys())
        if reserved:
            raise ValueError(
                "env cannot define Runtime-reserved variables: " + ", ".join(reserved)
            )
        env["PLUGIN_ROOT"] = str(root)
        env["PLUGIN_DATA"] = str(data_root)
        server = MCPStdio(
            name=source_id,
            transport="stdio",
            command=[command],
            args=parsed.args,
            env=env,
            cwd=str(cwd),
        )
        diagnostics = _unsupported_mcp_field_diagnostics(
            parsed.model_extra or {}, source_id, config_path
        )
        return server, diagnostics

    parsed_http = _CodexHTTPServer.model_validate(raw)
    auth = (
        MCPStaticAuth(
            headers=parsed_http.headers, api_key_env=parsed_http.bearer_token_env_var
        )
        if parsed_http.bearer_token_env_var is not None
        else MCPOAuth(type="oauth", scopes=[])
        if parsed_http.oauth_resource is not None
        else MCPStaticAuth(headers=parsed_http.headers)
    )
    server = MCPHttp(
        name=source_id,
        transport="http",
        url=normalize_mcp_server_url(parsed_http.url),
        auth=auth,
    )
    extras = dict(parsed_http.model_extra or {})
    if parsed_http.oauth_resource is not None:
        extras["oauth_resource"] = parsed_http.oauth_resource
    diagnostics = _unsupported_mcp_field_diagnostics(extras, source_id, config_path)
    return server, diagnostics


def _unsupported_mcp_field_diagnostics(
    extras: Mapping[str, object], source_id: str, config_path: Path
) -> tuple[PluginAdapterDiagnostic, ...]:
    ignored = sorted(set(extras) - {"note"})
    if not ignored:
        return ()
    return (
        PluginAdapterDiagnostic(
            severity="info",
            code="plugin.compatibility.codex.mcp_metadata_partially_supported",
            path=config_path,
            message=(
                f"Codex MCP server {source_id!r} fields {', '.join(ignored)} are "
                "retained privately but have no direct Runtime equivalent."
            ),
            fatal=False,
            component="mcp_server",
        ),
    )


def _resolve_command(root: Path, command: str) -> str:
    if command.startswith("./"):
        resolved = (root / command[2:]).resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise ValueError("stdio command must resolve inside the plugin root")
        return str(resolved)
    if "/" in command or "\\" in command or command in {".", ".."}:
        raise ValueError("stdio command must be a bare executable or start with './'")
    return command


def _resolve_cwd(root: Path, value: str | None) -> Path:
    if value is None or value in {".", "./"}:
        return root
    relative = PurePosixPath(value.removeprefix("./"))
    if "\\" in value or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("stdio cwd must resolve inside the plugin root")
    resolved = root.joinpath(*relative.parts).resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("stdio cwd must resolve inside the plugin root")
    return resolved


def _safe_error(error: BaseException) -> str:
    if not isinstance(error, ValidationError):
        return str(error)
    parts = []
    for issue in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in issue["loc"])
        parts.append(f"{location}: {issue['msg']}" if location else issue["msg"])
    return "; ".join(parts)
