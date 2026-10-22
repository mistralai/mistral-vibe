from __future__ import annotations

import copy
import tomllib
from typing import TYPE_CHECKING, Any

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers._base import _internal_toml_document
from vibe.core.config.types import LayerConfigSnapshot

if TYPE_CHECKING:
    from vibe.core.config.orchestrator import ConfigOrchestrator


class AdminConfigLayer(ConfigLayer[RawConfig]):
    """Highest-priority, in-memory layer holding org-enforced config.

    Populated at runtime from the admin-managed config endpoint. Values it sets
    shadow every other layer in the merged config; that precedence is the whole
    enforcement mechanism. The layer itself never persists: every session build
    loads it from the managed-config cache, and a session start from the
    endpoint's answer. Read-only from the patch path.
    """

    NAME = "admin"

    def __init__(self, *, data: dict[str, Any] | None = None, name: str = NAME) -> None:
        super().__init__(name=name)
        self._data: dict[str, Any] = copy.deepcopy(data) if data else {}

    @property
    def enforced_keys(self) -> list[str]:
        return sorted(self._data)

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        data = copy.deepcopy(self._data)
        fingerprint = create_dict_fingerprint(data)
        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError("AdminConfigLayer is enforced and read-only")

    def load_managed_toml(self, toml_text: str) -> None:
        """Replace the in-memory admin config from a TOML document."""
        self._data = _internal_toml_document(tomllib.loads(toml_text))

    def snapshot(self) -> dict[str, Any]:
        return self._data

    def restore(self, data: dict[str, Any]) -> None:
        self._data = data


def admin_layer(orchestrator: ConfigOrchestrator[Any]) -> AdminConfigLayer | None:
    try:
        layer = orchestrator.get_layer(AdminConfigLayer.NAME)
    except KeyError:
        return None
    return layer if isinstance(layer, AdminConfigLayer) else None


def enforced_keys(orchestrator: ConfigOrchestrator[Any]) -> list[str]:
    """The keys the org config currently enforces; empty without an admin layer."""
    layer = admin_layer(orchestrator)
    return layer.enforced_keys if layer is not None else []
