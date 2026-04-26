from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from vibe.core.logger import logger
from vibe.core.plugins.models import (
    PLUGIN_NAME_PATTERN,
    MarketplaceIndex,
    PluginEntry,
    PluginManifest,
    PluginScope,
    PluginSource,
)
from vibe.core.plugins.registry import PluginRegistryManager

_VALID_PLUGIN_NAME = re.compile(PLUGIN_NAME_PATTERN)

_GITHUB_TREE_RE = re.compile(
    r"^(?P<base>https://github\.com/[^/]+/[^/]+?)(?:\.git)?(?:/tree/(?P<ref>[^/]+)(?:/(?P<subdir>.+?))?)?/?(?:\.git)?$"
)


@dataclass(frozen=True, slots=True)
class _GitUrl:
    clone_url: str
    ref: str | None = None
    subdir: str | None = None


class PluginInstaller:
    """Installs and removes plugins from git, local paths, or a marketplace."""

    def __init__(self, registry_manager: PluginRegistryManager) -> None:
        self._registry = registry_manager

    def install_from_git(
        self, url: str, ref: str | None = None, *, scope: PluginScope = PluginScope.USER
    ) -> PluginManifest:
        parsed = self._parse_github_url(url)
        effective_ref = ref or parsed.ref
        target_dir = self._registry.get_plugins_dir_for_scope(scope)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "plugin"
            self._git_clone(parsed.clone_url, tmp_path, effective_ref)
            if parsed.subdir:
                manifest_dir = (tmp_path / parsed.subdir).resolve()
                if not manifest_dir.is_relative_to(tmp_path.resolve()):
                    msg = f"Invalid subdirectory in URL: {parsed.subdir!r}"
                    raise ValueError(msg)
            else:
                manifest_dir = tmp_path
            manifest = PluginManifest.from_dir(manifest_dir)
            dest = target_dir / manifest.name
            if dest.is_symlink():
                dest.unlink()
            elif dest.exists():
                shutil.rmtree(dest)
            shutil.move(manifest_dir, dest)

        try:
            self._registry.register(
                PluginEntry(
                    name=manifest.name,
                    version=manifest.version,
                    source=PluginSource.GIT,
                    source_uri=url,
                    enabled=True,
                    installed_at=datetime.now(UTC).isoformat(),
                    pinned_ref=effective_ref,
                ),
                scope=scope,
            )
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        logger.info("Installed plugin '%s' from git", manifest.name)
        return manifest

    def install_from_local(
        self, path: Path, *, scope: PluginScope = PluginScope.USER
    ) -> PluginManifest:
        target_dir = self._registry.get_plugins_dir_for_scope(scope)
        resolved = path.resolve()
        manifest = PluginManifest.from_dir(resolved)
        link = target_dir / manifest.name
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.exists():
            shutil.rmtree(link)
        link.symlink_to(resolved, target_is_directory=True)

        try:
            self._registry.register(
                PluginEntry(
                    name=manifest.name,
                    version=manifest.version,
                    source=PluginSource.LOCAL,
                    source_uri=str(resolved),
                    enabled=True,
                    installed_at=datetime.now(UTC).isoformat(),
                ),
                scope=scope,
            )
        except Exception:
            if link.is_symlink():
                link.unlink()
            raise
        logger.info("Installed plugin '%s' from local path", manifest.name)
        return manifest

    def install_from_marketplace(
        self,
        plugin_name: str,
        marketplace_dir: Path,
        *,
        scope: PluginScope = PluginScope.USER,
    ) -> PluginManifest:
        target_dir = self._registry.get_plugins_dir_for_scope(scope)
        index = MarketplaceIndex.from_dir(marketplace_dir)
        ref = next((p for p in index.plugins if p.name == plugin_name), None)
        if ref is None:
            msg = f"Plugin '{plugin_name}' not found in marketplace index"
            raise KeyError(msg)

        source_dir = (marketplace_dir / index.plugin_root / ref.source).resolve()
        if not source_dir.is_relative_to(marketplace_dir.resolve()):
            msg = f"Invalid source path '{ref.source}' in marketplace index"
            raise ValueError(msg)
        dest = target_dir / plugin_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_dir, dest)

        try:
            manifest = PluginManifest.from_dir(dest)
        except FileNotFoundError:
            manifest = PluginManifest(
                name=ref.name,
                description=ref.description or f"Plugin {ref.name}",
                version=ref.version,
            )
            manifest.mcp_servers.extend(PluginManifest.load_mcp_json(dest))
        if manifest.name != plugin_name:
            logger.warning(
                "Manifest name '%s' differs from marketplace name '%s'; using marketplace name",
                manifest.name,
                plugin_name,
            )
        try:
            self._registry.register(
                PluginEntry(
                    name=plugin_name,
                    version=manifest.version,
                    source=PluginSource.MARKETPLACE,
                    source_uri=ref.source,
                    enabled=True,
                    installed_at=datetime.now(UTC).isoformat(),
                ),
                scope=scope,
            )
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        logger.info("Installed plugin '%s' from marketplace", manifest.name)
        return manifest

    def remove(self, name: str) -> None:
        if not _VALID_PLUGIN_NAME.match(name):
            msg = f"Invalid plugin name: {name!r}"
            raise ValueError(msg)
        plugin_dir = self._registry.get_plugin_dir(name)
        if plugin_dir is not None:
            if plugin_dir.is_symlink():
                plugin_dir.unlink()
            elif plugin_dir.exists():
                shutil.rmtree(plugin_dir)
        self._registry.unregister(name)
        logger.info("Removed plugin '%s'", name)

    def update(self, name: str) -> PluginManifest | None:
        """Update a git-sourced plugin by re-cloning from its source URI.

        Returns the new manifest if updated, or ``None`` if the plugin
        is not git-sourced or was already up to date.
        """
        if not _VALID_PLUGIN_NAME.match(name):
            msg = f"Invalid plugin name: {name!r}"
            raise ValueError(msg)
        plugins = self._registry.get_all_plugins_with_scope()
        if name not in plugins:
            msg = f"Plugin '{name}' is not installed"
            raise KeyError(msg)
        scope, entry = plugins[name]
        if entry.source != PluginSource.GIT or not entry.source_uri:
            logger.info("Plugin '%s' is not git-sourced; skipping update", name)
            return None

        old_version = entry.version
        manifest = self.install_from_git(
            entry.source_uri, entry.pinned_ref, scope=scope
        )
        if manifest.version == old_version:
            logger.info("Plugin '%s' is already up to date (%s)", name, old_version)
        else:
            logger.info(
                "Updated plugin '%s': %s -> %s", name, old_version, manifest.version
            )
        return manifest

    def update_all(self) -> list[tuple[str, str, str]]:
        """Update all git-sourced plugins.

        Returns a list of ``(name, old_version, new_version)`` for plugins that changed.
        """
        updated: list[tuple[str, str, str]] = []
        for name, entry in self._registry.get_all_plugins().items():
            if entry.source != PluginSource.GIT or not entry.source_uri:
                continue
            old_version = entry.version
            try:
                manifest = self.update(name)
                if manifest and manifest.version != old_version:
                    updated.append((name, old_version, manifest.version))
            except (
                subprocess.CalledProcessError,
                FileNotFoundError,
                ValueError,
                OSError,
                KeyError,
            ):
                logger.warning("Failed to update plugin '%s'", name, exc_info=True)
        return updated

    @staticmethod
    def _parse_github_url(url: str) -> _GitUrl:
        if m := _GITHUB_TREE_RE.match(url):
            return _GitUrl(
                clone_url=m.group("base"), ref=m.group("ref"), subdir=m.group("subdir")
            )
        return _GitUrl(clone_url=url)

    @staticmethod
    def _git_clone(url: str, dest: Path, ref: str | None) -> None:
        if url.startswith("-"):
            msg = "Invalid git URL"
            raise ValueError(msg)
        if ref and ref.startswith("-"):
            msg = "Invalid git ref"
            raise ValueError(msg)
        cmd: list[str] = ["git", "clone", "--depth", "1"]
        if ref:
            cmd.extend(["--branch", ref])
        cmd.extend(["--", url, str(dest)])
        subprocess.run(cmd, check=True)
