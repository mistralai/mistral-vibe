from __future__ import annotations

import argparse
from pathlib import Path

from rich import print as rprint

from vibe.core.plugins.installer import PluginInstaller
from vibe.core.plugins.marketplace import MarketplaceManager
from vibe.core.plugins.models import MarketplaceConfig, PluginManifest, PluginScope
from vibe.core.plugins.registry import PluginRegistryManager


def build_plugin_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibe plugin", description="Manage vibe plugins"
    )
    sub = parser.add_subparsers(dest="plugin_command")

    install_p = sub.add_parser("install", help="Install a plugin")
    install_p.add_argument(
        "source", help="Git URL, local path, or marketplace plugin name"
    )
    install_p.add_argument("--ref", help="Git branch or tag to pin")
    install_p.add_argument(
        "--local", action="store_true", help="Install from local path (symlink)"
    )
    install_p.add_argument(
        "--marketplace",
        metavar="URL",
        help="Marketplace URL or owner/repo to install from",
    )
    install_p.add_argument(
        "--scope",
        choices=["user", "project", "local"],
        default="user",
        help="Installation scope",
    )

    remove_p = sub.add_parser("remove", help="Remove an installed plugin")
    remove_p.add_argument("name", help="Plugin name")

    sub.add_parser("list", help="List installed plugins")

    enable_p = sub.add_parser("enable", help="Enable a plugin")
    enable_p.add_argument("name", help="Plugin name")

    disable_p = sub.add_parser("disable", help="Disable a plugin")
    disable_p.add_argument("name", help="Plugin name")

    search_p = sub.add_parser("search", help="Search marketplace for plugins")
    search_p.add_argument("query", help="Search query")
    search_p.add_argument(
        "--marketplace", metavar="URL", help="Marketplace URL or owner/repo"
    )

    info_p = sub.add_parser("info", help="Show info about an installed plugin")
    info_p.add_argument("name", help="Plugin name")

    update_p = sub.add_parser("update", help="Update git-sourced plugins")
    update_p.add_argument("name", nargs="?", help="Plugin name (omit to update all)")

    marketplace_p = sub.add_parser("marketplace", help="Manage plugin marketplaces")
    marketplace_sub = marketplace_p.add_subparsers(dest="marketplace_command")

    mp_add = marketplace_sub.add_parser("add", help="Add a marketplace")
    mp_add.add_argument("url", help="Marketplace URL or owner/repo")
    mp_add.add_argument("--name", help="Display name (auto-detected if omitted)")

    mp_remove = marketplace_sub.add_parser("remove", help="Remove a marketplace")
    mp_remove.add_argument("url_or_name", help="Marketplace URL or name")

    marketplace_sub.add_parser("list", help="List configured marketplaces")

    mp_update = marketplace_sub.add_parser("update", help="Re-fetch marketplace caches")
    mp_update.add_argument(
        "url_or_name", nargs="?", help="Marketplace URL or name (omit for all)"
    )

    return parser


def handle_plugin_command(args: argparse.Namespace) -> None:
    from vibe.core.config.harness_files._harness_manager import (
        _get_plugin_registry_manager,
    )

    registry = _get_plugin_registry_manager()
    installer = PluginInstaller(registry)

    match args.plugin_command:
        case "install":
            _handle_install(args, installer, registry)
        case "remove":
            installer.remove(args.name)
            rprint(f"[green]Removed plugin '{args.name}'[/]")
        case "list":
            _handle_list(registry)
        case "enable":
            registry.set_enabled(args.name, enabled=True)
            rprint(f"[green]Enabled plugin '{args.name}'[/]")
        case "disable":
            registry.set_enabled(args.name, enabled=False)
            rprint(f"[yellow]Disabled plugin '{args.name}'[/]")
        case "search":
            _handle_search(args)
        case "info":
            _handle_info(args, registry)
        case "update":
            _handle_update(args, installer)
        case "marketplace":
            _handle_marketplace(args)
        case _:
            rprint("[red]Unknown plugin command. Use --help for usage.[/]")


def _handle_install(
    args: argparse.Namespace,
    installer: PluginInstaller,
    registry: PluginRegistryManager,
) -> None:
    scope = PluginScope(args.scope)
    try:
        registry.get_plugins_dir_for_scope(scope)
    except KeyError:
        if scope in {PluginScope.PROJECT, PluginScope.LOCAL}:
            rprint(
                f"[red]Scope '{scope.value}' requires running inside a trusted "
                "project directory.[/]"
            )
            rprint(
                "[dim]Hint: run this command in a trusted project workdir or use "
                "'--scope user'.[/]"
            )
            return
        raise

    if args.local:
        manifest = installer.install_from_local(Path(args.source), scope=scope)
        rprint(f"[green]Installed plugin '{manifest.name}' v{manifest.version}[/]")
        return
    marketplace_mgr = MarketplaceManager()

    if args.marketplace:
        config = MarketplaceConfig(url=args.marketplace)
        marketplace_mgr.fetch(config)
        cache_dir = marketplace_mgr.get_cache_dir_for(config)
        manifest = installer.install_from_marketplace(
            args.source, cache_dir, scope=scope
        )
        rprint(f"[green]Installed plugin '{manifest.name}' v{manifest.version}[/]")
        return

    source = args.source
    # URL-like source: try auto-detecting marketplace
    if "/" in source:
        config = MarketplaceConfig(url=source)
        if marketplace_mgr.is_marketplace(config):
            _marketplace_add(source, name=None)
            rprint(
                "[dim]Hint: install a plugin from this marketplace with "
                "'vibe plugin install <name>'[/]"
            )
            return
        manifest = installer.install_from_git(source, ref=args.ref, scope=scope)
        rprint(f"[green]Installed plugin '{manifest.name}' v{manifest.version}[/]")
        return

    # Bare plugin name: search configured marketplaces
    configs = _load_marketplace_configs()
    if configs:
        if result := marketplace_mgr.find_plugin(configs, source):
            mp_config, _ = result
            cache_dir = marketplace_mgr.get_cache_dir_for(mp_config)
            manifest = installer.install_from_marketplace(
                source, cache_dir, scope=scope
            )
            label = mp_config.name or mp_config.url
            rprint(
                f"[green]Installed plugin '{manifest.name}' v{manifest.version} from {label}[/]"
            )
            return

    rprint(f"[red]Plugin '{source}' not found in configured marketplaces.[/]")
    rprint("[dim]Hint: add a marketplace with 'vibe plugin marketplace add <url>'[/]")


def _handle_list(registry: PluginRegistryManager) -> None:
    plugins = registry.get_all_plugins_with_scope()
    if not plugins:
        rprint("[dim]No plugins installed.[/]")
        return
    for name, (scope, entry) in plugins.items():
        status = "[green]enabled[/]" if entry.enabled else "[yellow]disabled[/]"
        rprint(f"  {name} v{entry.version} ({entry.source}) [{scope}] {status}")


def _handle_search(args: argparse.Namespace) -> None:
    marketplace_mgr = MarketplaceManager()
    if args.marketplace:
        config = MarketplaceConfig(url=args.marketplace)
        index = marketplace_mgr.fetch(config)
        results = marketplace_mgr.search(index, args.query)
        if not results:
            rprint("[dim]No plugins found matching your query.[/]")
            return
        for ref in results:
            rprint(f"  [bold]{ref.name}[/] v{ref.version} — {ref.description}")
    else:
        configs = _load_marketplace_configs()
        if not configs:
            rprint(
                "[dim]No marketplaces configured. Use 'vibe plugin marketplace add <url>' to add one.[/]"
            )
            return
        results = marketplace_mgr.search_all(configs, args.query)
        if not results:
            rprint("[dim]No plugins found matching your query.[/]")
            return
        for config, ref in results:
            label = config.name or config.url
            rprint(
                f"  [bold]{ref.name}[/] v{ref.version} ({label}) — {ref.description}"
            )


def _handle_info(args: argparse.Namespace, registry: PluginRegistryManager) -> None:
    plugins_with_scope = registry.get_all_plugins_with_scope()
    hit = plugins_with_scope.get(args.name)
    if hit is None:
        rprint(f"[red]Plugin '{args.name}' is not installed.[/]")
        return
    scope, entry = hit
    rprint(f"[bold]{entry.name}[/] v{entry.version}")
    rprint(f"  Source: {entry.source} ({entry.source_uri})")
    rprint(f"  Scope: {scope}")
    rprint(f"  Enabled: {entry.enabled}")
    rprint(f"  Installed: {entry.installed_at}")
    plugin_dir = registry.get_plugin_dir(args.name)
    if plugin_dir is None:
        rprint("  [dim](plugin directory not found)[/]")
        return
    try:
        manifest = PluginManifest.from_dir(plugin_dir)
        if manifest.description:
            rprint(f"  Description: {manifest.description}")
        if manifest.author:
            rprint(f"  Author: {manifest.author}")
        resolved = manifest.resolve_paths(plugin_dir)
        if resolved.skill_dirs:
            items: list[str] = []
            for d in resolved.skill_dirs:
                if (d / "SKILL.md").is_file():
                    items.append(d.name)
                else:
                    items.extend(
                        p.name
                        for p in sorted(d.iterdir())
                        if p.is_dir() and (p / "SKILL.md").is_file()
                    )
            rprint(f"  Skills: {', '.join(items) if items else '(empty)'}")
        if resolved.agent_dirs:
            items = [
                p.stem
                for d in resolved.agent_dirs
                for p in sorted(d.iterdir())
                if p.is_file()
            ]
            rprint(f"  Agents: {', '.join(items) if items else '(empty)'}")
        if resolved.tool_dirs:
            items = [
                p.stem
                for d in resolved.tool_dirs
                for p in sorted(d.iterdir())
                if p.suffix == ".py" and p.stem != "__init__"
            ]
            rprint(f"  Tools: {', '.join(items) if items else '(empty)'}")
        if resolved.command_dirs:
            items = [
                p.stem
                for d in resolved.command_dirs
                for p in sorted(d.iterdir())
                if p.suffix == ".md"
            ]
            rprint(f"  Commands: {', '.join(items) if items else '(empty)'}")
        if manifest.mcp_servers:
            names = [s.get("name", "unnamed") for s in manifest.mcp_servers]
            rprint(f"  MCP servers: {', '.join(names)}")
    except FileNotFoundError:
        rprint("  [dim](no manifest or discoverable content in plugin directory)[/]")


def _load_marketplace_configs() -> list[MarketplaceConfig]:
    """Read marketplace configs directly from TOML without full VibeConfig validation."""
    from vibe.core.config._settings import TomlFileSettingsSource, VibeConfig

    raw = TomlFileSettingsSource(VibeConfig).toml_data
    raw_list = raw.get("plugin_marketplaces", [])
    return [MarketplaceConfig.model_validate(item) for item in raw_list]


def _save_marketplace_configs(configs: list[MarketplaceConfig]) -> None:
    from vibe.core.config._settings import VibeConfig

    VibeConfig.save_updates({
        "plugin_marketplaces": [c.model_dump(exclude_none=True) for c in configs]
    })


def _handle_marketplace(args: argparse.Namespace) -> None:
    match args.marketplace_command:
        case "add":
            _marketplace_add(args.url, args.name)
        case "remove":
            _marketplace_remove(args.url_or_name)
        case "list":
            _marketplace_list()
        case "update":
            _marketplace_update(getattr(args, "url_or_name", None))
        case _:
            rprint("[red]Unknown marketplace command. Use --help for usage.[/]")


def _marketplace_add(url: str, name: str | None) -> None:
    marketplace_mgr = MarketplaceManager()
    config = MarketplaceConfig(url=url, name=name or "")
    configs = _load_marketplace_configs()
    normalized = marketplace_mgr.normalize_url(url)
    for existing in configs:
        if marketplace_mgr.normalize_url(existing.url) == normalized:
            rprint(f"[yellow]Marketplace '{url}' is already configured.[/]")
            return
    index = marketplace_mgr.fetch(config)
    if not config.name:
        config = MarketplaceConfig(url=url, name=index.name)
    configs.append(config)
    _save_marketplace_configs(configs)
    rprint(f"[green]Added marketplace '{config.name}' ({url})[/]")
    if index.plugins:
        rprint(f"  {len(index.plugins)} plugin(s) available:")
        for ref in index.plugins:
            rprint(f"    [bold]{ref.name}[/] v{ref.version} — {ref.description}")


def _marketplace_remove(url_or_name: str) -> None:
    configs = _load_marketplace_configs()
    marketplace_mgr = MarketplaceManager()
    normalized = (
        marketplace_mgr.normalize_url(url_or_name) if "/" in url_or_name else None
    )
    remaining = [
        c
        for c in configs
        if c.name != url_or_name
        and (normalized is None or marketplace_mgr.normalize_url(c.url) != normalized)
        and c.url != url_or_name
    ]
    if len(remaining) == len(configs):
        rprint(f"[red]Marketplace '{url_or_name}' not found.[/]")
        return
    _save_marketplace_configs(remaining)
    rprint(f"[green]Removed marketplace '{url_or_name}'.[/]")


def _marketplace_list() -> None:
    configs = _load_marketplace_configs()
    if not configs:
        rprint(
            "[dim]No marketplaces configured. Use 'vibe plugin marketplace add <url>' to add one.[/]"
        )
        return
    for config in configs:
        label = config.name or config.url
        rprint(f"  [bold]{label}[/] — {config.url}")


def _marketplace_update(url_or_name: str | None) -> None:
    configs = _load_marketplace_configs()
    if not configs:
        rprint("[dim]No marketplaces configured.[/]")
        return
    marketplace_mgr = MarketplaceManager()
    targets = configs
    if url_or_name:
        normalized = (
            marketplace_mgr.normalize_url(url_or_name) if "/" in url_or_name else None
        )
        targets = [
            c
            for c in configs
            if c.name == url_or_name
            or (normalized and marketplace_mgr.normalize_url(c.url) == normalized)
        ]
        if not targets:
            rprint(f"[red]Marketplace '{url_or_name}' not found.[/]")
            return
    for config in targets:
        marketplace_mgr.fetch(config, force=True)
        rprint(f"[green]Updated marketplace '{config.name or config.url}'.[/]")


def _handle_update(args: argparse.Namespace, installer: PluginInstaller) -> None:
    if args.name:
        manifest = installer.update(args.name)
        if manifest:
            rprint(f"[green]Updated plugin '{args.name}' to v{manifest.version}[/]")
        else:
            rprint(
                f"[dim]Plugin '{args.name}' is not git-sourced; nothing to update.[/]"
            )
    else:
        updated = installer.update_all()
        if not updated:
            rprint("[dim]All plugins are up to date.[/]")
            return
        for name, old_v, new_v in updated:
            rprint(f"[green]Updated '{name}': {old_v} -> {new_v}[/]")
