"""Model aliases: other names that resolve to a model's key.

A pin on an alias selects the aliased model, so a selection saved under an old
name survives a rename. Aliases never take a name away from a model: an alias
that is itself a model key, or that two models claim, resolves to nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from vibe.observability.logging import logger

# The config is rebuilt several times per session: warn once, not per rebuild.
_warned_aliases: set[tuple[str, str]] = set()


@dataclass(frozen=True, slots=True)
class AliasIndex:
    targets: dict[str, str]
    # Alias → why it resolves to nothing.
    dropped: dict[str, str]


def build_alias_index(
    aliases_by_key: Mapping[str, Sequence[str]], model_keys: set[str]
) -> AliasIndex:
    claims: defaultdict[str, list[str]] = defaultdict(list)
    for key, aliases in aliases_by_key.items():
        for alias in aliases:
            # A blank alias would match the unpinned active_model sentinel ("")
            # and hijack the default for every unpinned user.
            if not alias.strip():
                continue
            if alias != key and key not in claims[alias]:
                claims[alias].append(key)

    targets: dict[str, str] = {}
    dropped: dict[str, str] = {}
    for alias, keys in claims.items():
        if alias in model_keys:
            dropped[alias] = f"it is the ID of model '{alias}'"
        elif len(keys) > 1:
            dropped[alias] = f"models {', '.join(repr(k) for k in keys)} all claim it"
        else:
            targets[alias] = keys[0]
    return AliasIndex(targets=targets, dropped=dropped)


def warn_ignored_alias(alias: str, reason: str) -> None:
    """Log an ignored alias once per process."""
    if (alias, reason) in _warned_aliases:
        return
    _warned_aliases.add((alias, reason))
    logger.warning("Model alias '%s' is ignored: %s.", alias, reason)
