"""Shared validation and wire behavior for the Session API."""

from typing import Any

from pydantic import SerializationInfo

from ....session_values import ForwardCompatibleModel as ForwardCompatibleModel
from ....session_values import SessionModel as SessionModel

type JsonSchema = dict[str, Any]

_ORIGINAL_TYPE_KEYS = frozenset({"original_type", "originalType"})
_WIRE_TYPE_KEYS = _ORIGINAL_TYPE_KEYS | {"type"}


def read_unknown_variant(value: object) -> object:
    """The fields of a reader fallback for a wire object whose ``type`` this reader does not know.

    The object's ``type`` goes to ``original_type`` and ``type`` becomes ``unknown``. An object
    already in that shape, as built in Python, is returned as it is.
    """
    if not isinstance(value, dict):
        return value
    has_original_type = "original_type" in value or "originalType" in value
    if has_original_type and value.get("type", "unknown") == "unknown":
        return value
    # The object's own field of that name, if any, would hide its real type, so it is dropped.
    fields = {key: item for key, item in value.items() if key not in _ORIGINAL_TYPE_KEYS}
    return {**fields, "type": "unknown", "original_type": value.get("type")}


def write_unknown_variant(
    fields: dict[str, Any],
    original_type: str,
    extra: dict[str, Any] | None,
    info: SerializationInfo,
) -> dict[str, Any]:
    """The wire object of a reader fallback: its real ``type`` and no ``original_type``.

    ``unknown`` is only this reader's name for it. A stored or relayed copy must keep the
    real type, so that a reader that knows the type reads it. It must also keep the fields
    as they came, so a field sent as null stays null even when the dump excludes none.
    """
    if info.exclude_none:
        excluded = info.exclude or ()
        kept_nulls = {
            key: None
            for key, item in (extra or {}).items()
            if item is None
            and key not in excluded
            and (info.include is None or key in info.include)
        }
        fields = {**fields, **kept_nulls}
    return {
        "type": original_type,
        **{key: item for key, item in fields.items() if key not in _WIRE_TYPE_KEYS},
    }
