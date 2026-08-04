from __future__ import annotations

from vibe.core.tools.base import BaseTool
from vibe.core.tools.connectors.connector_registry import (
    ConnectorAuthAction,
    ConnectorRegistry,
    RemoteTool,
    _normalize_name,
    create_connector_proxy_tool_class,
)


class FakeConnectorRegistry(ConnectorRegistry):
    """Test double that returns canned connector tools without hitting the API."""

    def __init__(
        self,
        connectors: dict[str, list[RemoteTool]] | None = None,
        auth_actions: dict[str, ConnectorAuthAction] | None = None,
    ) -> None:
        super().__init__(api_key="fake-key")
        self._fake_connectors = connectors or {}
        self._fake_auth_actions = auth_actions or {}
        self._build_cache()

    def _build_cache(self) -> None:
        self._cache = {}
        self._connector_names = []
        self._connector_connected = {}
        self._connector_auth_action = {}
        self._alias_to_id = {}
        for connector_name, tools in self._fake_connectors.items():
            alias = _normalize_name(connector_name)
            connector_id = f"fake-id-{connector_name}"
            self._alias_to_id[alias] = connector_id
            tool_map: dict[str, type[BaseTool]] = {}
            for remote in tools:
                proxy_cls = create_connector_proxy_tool_class(
                    connector_name=connector_name,
                    connector_alias=alias,
                    connector_id=connector_id,
                    remote=remote,
                    api_key="fake-key",
                )
                tool_map[proxy_cls.get_name()] = proxy_cls
            self._cache[connector_id] = tool_map
            self._connector_names.append(alias)
            self._connector_connected[alias] = bool(tool_map)
            self._connector_auth_action[alias] = self._fake_auth_actions.get(
                connector_name, ConnectorAuthAction.NONE
            )

    def get_tools(self, *, force_refresh: bool = False) -> dict[str, type[BaseTool]]:
        if self._cache is None:
            self._build_cache()

        result: dict[str, type[BaseTool]] = {}
        if self._cache:
            for tools in self._cache.values():
                result.update(tools)
        return result

    async def get_tools_async(
        self, *, force_refresh: bool = False
    ) -> dict[str, type[BaseTool]]:
        return self.get_tools(force_refresh=force_refresh)

    async def get_auth_url(self, alias: str) -> str | None:
        """Return a fake auth URL for connectors that have no tools (not connected)."""
        if not self.is_connected(alias):
            return f"https://fake-auth.example.com/{alias}"
        return None

    async def refresh_connector_async(self, alias: str) -> dict[str, type[BaseTool]]:
        """No-op refresh for tests."""
        return {}
