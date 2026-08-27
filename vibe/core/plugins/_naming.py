"""Core-safe names for the tool groups a plugin's MCP sources produce.

Core requires unique ASCII TypeScript identifiers for group names and a plugin
declares none, so they are derived. A derived name is digested into the snapshot
and targeted by hooks, so it has to come out the same on every host: names are
assigned from the whole set of identities at once, ordered by raw identity, so
discovery order cannot reach the result.

The algorithm is the one in `docs/design/unified-harness-mcp-support.md`
("Naming and identity"), matching `resolveMcpGroupNames` in the TypeScript
runtime's `plugins/materializer.ts` step for step. The two runtimes share
fixtures, so a divergence here is a divergence in checked-in expected output.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib

from vibe.core.plugins._compatibility import typescript_identifier

_DIGEST_PREFIX = 8
_IDENTIFIER_CHARACTERS = frozenset("_$")


def identifier_segment(value: str) -> str:
    normalized = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in _IDENTIFIER_CHARACTERS)
        else "_"
        for character in value
    )
    return normalized or "_"


def plugin_mcp_group_name(plugin_namespace: str, source_id: str) -> str:
    return (
        f"plugin_{identifier_segment(plugin_namespace)}_{identifier_segment(source_id)}"
    )


@dataclass(frozen=True, slots=True)
class ToolGroupIdentity:
    plugin_name: str
    base_name: str
    source_id: str

    @property
    def key(self) -> str:
        return f"{self.plugin_name}\0{self.source_id}"

    @property
    def digest(self) -> str:
        identity = "\0".join((
            "plugin_mcp",
            self.plugin_name,
            self.base_name,
            self.source_id,
        ))
        return hashlib.sha256(identity.encode()).hexdigest()


def resolve_tool_group_names(
    identities: Iterable[ToolGroupIdentity], *, claimed: Iterable[str]
) -> dict[ToolGroupIdentity, str]:
    taken = set(claimed)
    by_base: dict[str, list[ToolGroupIdentity]] = {}
    for identity in sorted(set(identities), key=lambda item: item.key):
        by_base.setdefault(identity.base_name, []).append(identity)

    resolved: dict[ToolGroupIdentity, str] = {}
    for base_name in sorted(by_base):
        contenders = by_base[base_name]
        if len(contenders) == 1 and base_name not in taken:
            resolved[contenders[0]] = base_name
            taken.add(base_name)
            continue
        for identity in contenders:
            name = _with_digest_suffix(identity, taken)
            resolved[identity] = name
            taken.add(name)
    return resolved


def _with_digest_suffix(identity: ToolGroupIdentity, taken: set[str]) -> str:
    digest = identity.digest
    for length in range(_DIGEST_PREFIX, len(digest) + 1):
        name = f"{identity.base_name}_{digest[:length]}"
        if name not in taken:
            return name
    raise ValueError(f"cannot derive a unique group name for {identity.source_id!r}")


def tool_function_name(source_tool_name: str, override: str | None = None) -> str:
    return typescript_identifier(override if override is not None else source_tool_name)
