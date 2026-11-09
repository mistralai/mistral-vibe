"""Model aliases: other names that resolve to a model's key.

A pin or a settings entry saved under an old name survives a rename: the pin
selects the aliased model, and the settings entry applies to it. Aliases never
take a name away from a model: an alias that is itself a model key, or that two
models claim, resolves to nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vibe.core.config.models import (
    merge_model_payloads,
    normalize_model_configs,
    routed_model_keys,
)
from vibe.observability.logging import logger

# The config is rebuilt several times per session: log once, not per rebuild.
_warned_aliases: set[tuple[str, str]] = set()
_reported_settings: set[tuple[str, str]] = set()


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
        # A hand-edited alias map is validated only after this runs; skip
        # malformed entries here so a bad value is a schema error, not a
        # raw TypeError from iteration.
        if not isinstance(aliases, Sequence) or isinstance(aliases, str | bytes):
            continue
        for alias in aliases:
            if not isinstance(alias, str):
                continue
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


def report_aliased_settings(alias: str, target: str) -> None:
    """Log once per process that settings keyed on ``alias`` apply to ``target``."""
    if (alias, target) in _reported_settings:
        return
    _reported_settings.add((alias, target))
    logger.info("Model settings '%s' apply to model '%s'.", alias, target)


def defines_model(payload: object) -> bool:
    """Whether a ``models`` entry is a whole model, not a setting on another one.

    Without ``name`` and ``provider`` the entry cannot validate on its own: it
    adjusts a model some layer defines, like ``[models.<id>]`` or a field-only
    write.
    """
    return not isinstance(payload, Mapping) or {"name", "provider"} <= payload.keys()


CATALOG_LAYER = "model_catalog"


def catalog_alias_index(layers: Sequence[tuple[str, Mapping[str, Any]]]) -> AliasIndex:
    """The catalog's aliases over every layer's whole models.

    An alias any layer defines a whole model under keeps naming that model;
    so does a routed-model field, which injects its model without a ``models``
    entry. The alias map is read with the same replace-merge the merged
    ``catalog_model_aliases`` field gets: the topmost layer that defines it
    wins, normally the model catalog layer.
    """
    aliases: Mapping[str, Sequence[str]] = {}
    model_keys: set[str] = set()
    routed: dict[str, Any] = {}
    for _name, data in layers:
        raw = data.get("catalog_model_aliases")
        if isinstance(raw, Mapping):
            aliases = raw
        # The routed fields merge by replace per field, like the merged config:
        # derive their keys from the merged values, not per layer, so this
        # index and the validators cannot disagree.
        for field in (
            "routed_default_model",
            "routed_model_config",
            "routed_extra_models",
        ):
            if field in data:
                routed[field] = data[field]
        models = normalize_model_configs(data.get("models"))
        if isinstance(models, Mapping):
            model_keys.update(
                key for key, payload in models.items() if defines_model(payload)
            )
    model_keys.update(routed_model_keys(routed))
    return build_alias_index(
        {key: names for key, names in aliases.items() if key in model_keys}, model_keys
    )


def settings_keys(
    models: Mapping[str, Any], key: str, targets: Mapping[str, str]
) -> list[str]:
    """The keys a layer holding ``models`` keeps model ``key``'s settings under,
    the one that wins first.

    Its own key, then the aliases in sorted order. A pick is written to the
    first, so it is never masked by another entry for the same model.
    """
    aliases = sorted(
        alias
        for alias, payload in models.items()
        if targets.get(alias) == key and not defines_model(payload)
    )
    return [key, *aliases] if key in models else aliases


def models_on_catalog_ids(
    models: Mapping[str, Any], targets: Mapping[str, str]
) -> dict[str, Any]:
    """``models`` with each settings entry keyed on an alias moved to its model."""
    moved = {
        targets[key]
        for key, payload in models.items()
        if key in targets and not defines_model(payload)
    }
    if not moved:
        return dict(models)
    result = {
        key: payload
        for key, payload in models.items()
        if key not in targets or defines_model(payload)
    }
    for target in moved:
        merged: dict[str, Any] = {}
        for key in reversed(settings_keys(models, target, targets)):
            payload = models[key]
            if key != target:
                report_aliased_settings(key, target)
                # Normalizing keyed the entry's own alias on the old name.
                payload = {**payload, "alias": target}
            merged = merge_model_payloads(merged, payload)
        result[target] = merged
    return result
