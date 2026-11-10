"""Whether a plugin is on, and whether a toggle may change that.

``disabled_plugins`` turns a plugin off; ``enabled_plugins`` turns on one that
is off by default. Both lists add up across config layers, and an ``off``
wins over an ``on``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from vibe.core.skills.models import SkillScope

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.orchestrator import ConfigOrchestrator, FieldsMutation
    from vibe.core.plugins._native import PluginDescriptor

_REQUIRED_BUILTIN_PLUGINS = frozenset({"vibe"})


def is_required_plugin(plugin: PluginDescriptor) -> bool:
    """Vibe's own built-in plugin carries the product's skills and stays on."""
    return (
        plugin.scope is SkillScope.BUILTIN and plugin.name in _REQUIRED_BUILTIN_PLUGINS
    )


def is_plugin_enabled(plugin: PluginDescriptor, config: VibeConfigSchema) -> bool:
    if is_required_plugin(plugin):
        return True
    if plugin.name in config.disabled_plugins:
        return False
    return plugin.default_enabled or plugin.name in config.enabled_plugins


@dataclass(frozen=True, slots=True)
class PluginToggles:
    config: VibeConfigSchema
    # The writable layer's own ``disabled_plugins``: the list concatenates
    # across layers, so only the names this layer holds can be removed here.
    own_disabled: Sequence[str]

    def locked(self, plugin: PluginDescriptor) -> bool:
        """Vibe's own, or another layer turns it off: the user's ``on`` cannot win.

        The merged list keeps every layer's entries, so a name it holds more
        often than the writable layer does is still off once the user's go.
        """
        name = plugin.name
        return is_required_plugin(plugin) or (
            self.config.disabled_plugins.count(name) > self.own_disabled.count(name)
        )


async def set_plugin_enabled(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    plugin: PluginDescriptor,
    enabled: bool,
) -> FieldsMutation:
    """Turning on removes an ``off`` and, for an off-by-default plugin, adds an ``on``.

    Turning off does the reverse, so the user layer never holds both. Both lists
    are written together.
    """
    name = plugin.name

    def without(current: Any) -> list[str]:
        return [str(entry) for entry in current or [] if str(entry) != name]

    def with_name(current: Any) -> list[str]:
        return [*without(current), name]

    mutations = {"/disabled_plugins": without if enabled else with_name}
    if not plugin.default_enabled:
        mutations["/enabled_plugins"] = with_name if enabled else without
    return await orchestrator.mutate_fields(
        mutations, reason="toggle a local plugin from the Plugins page", default=[]
    )
