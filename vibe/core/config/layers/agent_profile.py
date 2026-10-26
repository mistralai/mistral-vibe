from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import copy
from typing import Any

from pydantic import BaseModel

from vibe.core.config._defaults import SHELL_TOOLS
from vibe.core.config.builder import InvalidConfigError
from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.shell_timeouts import (
    check_shell_timeout_pair,
    shell_timeout_seconds,
)
from vibe.core.config.types import LayerConfigSnapshot
from vibe.observability.logging import logger

# Fields an agent profile must never override. These route credentials -- the
# account call sends the API key to console_base_url, teleport sends it to
# vibe_code_sessions_base_url -- so a profile from an untrusted checkout could
# otherwise redirect them to an attacker host and exfiltrate the key.
PROTECTED_FIELDS = frozenset({
    "vibe_base_url",
    "console_base_url",
    "vibe_code_sessions_base_url",
})


_SHELL_TIMEOUT_KEYS = ("default_timeout", "max_timeout_seconds")
_LAYER_DESCRIPTIONS = {
    "user-toml": "your config.toml",
    "user-supplied": "the user config",
    "project-toml": "the project's config.toml",
    "environment": "the VIBE_* environment",
    "overrides": "the session's overrides",
}


def _shell_timeouts(data: object, name: str) -> dict[str, object]:
    """The shell tool ``name``'s timeouts that one layer's sparse ``data`` sets."""
    dumped = data.model_dump() if isinstance(data, BaseModel) else data
    tools = dumped.get("tools") if isinstance(dumped, dict) else None
    shell = tools.get(name) if isinstance(tools, dict) else None
    if not isinstance(shell, dict):
        return {}
    return {
        key: shell[key] for key in _SHELL_TIMEOUT_KEYS if shell.get(key) is not None
    }


def _same_seconds(name: str, key: str, left: object, right: object) -> bool:
    """Whether two settings of one shell timeout are the same whole seconds, read
    exactly, as the schema reads them: never through a float, which would make
    two different timeouts above 2**53 seconds equal.
    """
    try:
        return shell_timeout_seconds(name, key, left) == shell_timeout_seconds(
            name, key, right
        )
    except ValueError as error:
        raise InvalidConfigError(str(error)) from error


def refuse_profile_shell_timeouts(
    profile_name: str,
    overrides: Mapping[str, object],
    lower_layers: Iterable[tuple[str, object]],
) -> None:
    """Refuse a profile whose shell timeouts would replace other ones a config set.

    The agent-profile layer outranks every config file, so a profile's
    ``default_timeout`` or ``max_timeout_seconds`` would otherwise win over the
    user's own without a word. ``lower_layers`` are the ``(name, data)`` of the
    layers below it, lowest first. The profile is compared with the timeouts
    they run together, each from the highest layer that sets it, and a refusal
    names that layer. A config that sets the same values, or none, is fine, and
    the configs below must set both or neither, as without an agent; the org's
    enforced config still outranks the profile. Raises the error clients
    show as a configuration error, which the user fixes in the named config.
    """
    lower = list(lower_layers)
    for name in SHELL_TOOLS:
        wanted = _shell_timeouts(overrides, name)
        if not wanted:
            continue
        # What the configs below run without the agent: each key as the highest
        # layer that sets it leaves it, with that layer's name. A value a higher
        # config already replaces is not one the agent would replace.
        effective: dict[str, object] = {}
        source: dict[str, str] = {}
        for layer_name, data in lower:
            for key, value in _shell_timeouts(data, name).items():
                effective[key] = value
                source[key] = layer_name
        clashes = [
            key
            for key in wanted
            if key in effective
            and not _same_seconds(name, key, effective[key], wanted[key])
        ]
        if clashes:
            # Name the config the first clashing timeout comes from, the one to fix.
            layer_name = source[clashes[0]]
            where = _LAYER_DESCRIPTIONS.get(layer_name, f"the {layer_name} config")
            agent_sets = " and ".join(f"{key} {wanted[key]}" for key in wanted)
            config_sets = " and ".join(
                f"{key} {effective[key]}"
                for key in clashes
                if source[key] == layer_name
            )
            raise InvalidConfigError(
                f"The '{profile_name}' agent runs tools.{name} with {agent_sets},"
                f" but {where} sets {config_sets}. An agent does not replace your"
                f" shell timeouts: remove them from {where}, or set them to the"
                " agent's, to use this agent."
            )
        # The agent's pair would complete one the configs set only half of, so
        # the configs below it are held to the same rule as without an agent.
        try:
            check_shell_timeout_pair(name, effective)
        except ValueError as error:
            raise InvalidConfigError(str(error)) from error


class AgentProfileLayer(ConfigLayer[RawConfig]):
    """In-memory layer holding the currently active agent profile overrides."""

    NAME = "agent-profile"

    def __init__(
        self,
        *,
        data: dict[str, Any] | None = None,
        name: str = NAME,
        profile_name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._data = self._strip_protected(copy.deepcopy(data or {}))
        # The running agent's name; ``None`` for a layer no agent runs on.
        self.profile_name = profile_name

    def check_lower_layers(self, lower: Sequence[tuple[str, dict[str, Any]]]) -> None:
        """Refuse a config whose shell timeouts the running agent would replace.

        Runs on every build, so a reload or a write that sets them is refused
        before it is live or on disk, as switching to the agent is.
        """
        if self.profile_name is not None:
            refuse_profile_shell_timeouts(self.profile_name, self._data, lower)

    @staticmethod
    def _strip_protected(data: dict[str, Any]) -> dict[str, Any]:
        if blocked := PROTECTED_FIELDS.intersection(data):
            logger.warning(
                "Ignoring protected field(s) %s in agent profile overrides",
                ", ".join(sorted(blocked)),
            )
            for field in blocked:
                del data[field]
        return data

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        data = copy.deepcopy(self._data)
        fingerprint = create_dict_fingerprint(data)
        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, next_config: RawConfig) -> str:
        self._data = self._strip_protected(copy.deepcopy(next_config.model_dump()))
        return create_dict_fingerprint(self._data)
