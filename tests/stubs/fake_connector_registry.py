from __future__ import annotations

from vibe.core.tools.base import BaseTool
from vibe.core.tools.connectors.connector_registry import (
    ConnectorRegistry,
    RemoteTool,
    _normalize_name,
    create_connector_proxy_tool_class,
)


class FakeConnectorRegistry(ConnectorRegistry):
    """Test double that returns canned connector tools without hitting the API."""

    def __init__(self, connectors: dict[str, list[RemoteTool]] | None = None) -> None:
        super().__init__(api_key="fake-key")
        self._fake_connectors = connectors or {}
        self._build_cache()

    def _build_cache(self) -> None:
        self._cache = {}
        self._connector_names = []
        self._connector_connected = {}
        for connector_name, tools in self._fake_connectors.items():
            alias = _normalize_name(connector_name)
            connector_id = f"fake-id-{connector_name}"
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

    def get_tools(self) -> dict[str, type[BaseTool]]:
        if self._cache is None:
            self._build_cache()

        result: dict[str, type[BaseTool]] = {}
        if self._cache:
            for tools in self._cache.values():
                result.update(tools)
        return result

    async def get_tools_async(self) -> dict[str, type[BaseTool]]:
        return self.get_tools()
