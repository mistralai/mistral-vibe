from __future__ import annotations

from vibe.core.tools.base import BaseTool
from vibe.core.tools.connectors.connector_registry import (
    ConnectorAuthAction,
    ConnectorCatalogEntry,
    ConnectorRegistry,
    ConnectorToolDefinition,
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
        bootstrap_error: str | None = None,
        connector_errors: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._fake_connectors = connectors or {}
        self._fake_auth_actions = auth_actions or {}
        self._build_cache()
        self._bootstrap_error = bootstrap_error
        self._connector_errors = connector_errors or {}

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
                    connector_alias=alias, remote=remote
                )
                tool_map[proxy_cls.get_name()] = proxy_cls
            self._cache[connector_id] = tool_map
            self._connector_names.append(alias)
            self._connector_connected[alias] = bool(tool_map)
            self._connector_auth_action[alias] = self._fake_auth_actions.get(
                connector_name, ConnectorAuthAction.NONE
            )

    def bootstrap_error(self) -> str | None:
        """The catalog failure the fake catalog service raises, if any."""
        return self._bootstrap_error

    async def get_auth_url(self, alias: str) -> str | None:
        """Return a fake auth URL for connectors that have no tools (not connected)."""
        if not self.is_connected(alias):
            return f"https://fake-auth.example.com/{alias}"
        return None

    def catalog_entries(self) -> tuple[ConnectorCatalogEntry, ...]:
        """Return the catalog represented by this fake executor."""
        return tuple(
            ConnectorCatalogEntry(
                connector_id=f"fake-id-{connector_name}",
                alias=_normalize_name(connector_name),
                display_name=connector_name,
                ready=bool(tools),
                auth_action=self._fake_auth_actions.get(
                    connector_name, ConnectorAuthAction.NONE
                ),
                tools=tuple(
                    ConnectorToolDefinition(
                        name=tool.name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                    )
                    for tool in tools
                ),
                diagnostic=self._connector_errors.get(_normalize_name(connector_name)),
            )
            for connector_name, tools in self._fake_connectors.items()
        )
