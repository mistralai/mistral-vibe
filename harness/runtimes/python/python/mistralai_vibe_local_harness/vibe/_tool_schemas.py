# ruff: noqa: I002 -- new modules here do without postponed annotations.
"""The provided-tool input schemas Core refuses, and the same schemas restricted to objects."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re

from pydantic import JsonValue

TOOL_SCHEMA_NOT_OBJECT = "tool_schema_not_object"
"""The code of the notice naming the tools a source listed this way."""


@dataclass(frozen=True, slots=True)
class ToolsLeftOutSignal:
    """An MCP server or a connector listed tools whose input schema's type is
    not ``object``, which the Runtime left out.
    """

    source_name: str
    tool_names: tuple[str, ...]

    @property
    def message(self) -> str:
        names = ", ".join(f"{name!r}" for name in self.tool_names)
        if len(self.tool_names) == 1:
            return (
                f"{self.source_name} tool {names} is unavailable because the "
                'type of its input schema is not "object".'
            )
        return (
            f"{self.source_name} tools {names} are unavailable because the "
            'type of their input schemas is not "object".'
        )


def type_is_not_object(schema: object) -> bool:
    """Whether ``schema`` sets top-level types other than ``object``.

    A tool call's arguments are an object, and the MCP specification requires
    a tool's ``inputSchema`` to have ``"type": "object"``, so Core refuses
    such a tool. The types come from ``type``, from the branches of an
    ``anyOf`` or ``oneOf`` (also nested), and from a local ``$ref``
    (``#/...``), read in place of the keywords next to it. A schema that sets
    no type, such as ``{}``, passes, and so does a ``$ref`` that does not
    resolve in the schema. The same rule as Core's, so a tool an MCP server or
    a connector lists this way is left out instead of failing the session.
    """
    types = _TopLevelTypes(schema).of(schema)
    return types is not None and types != ["object"]


_MAX_REF_DEPTH = 32
"""A ``$ref`` chain longer than this sets no type."""

_MISSING = object()

_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")


class _TopLevelTypes:
    """The types a schema allows at its top level, sorted, or ``None`` when it sets none.

    Each local ``$ref`` target is read once, so a schema that refers to the same
    target many times, as an ``anyOf`` of two ``$ref`` per level does, takes time
    in its size, not exponential in its depth. A ``$ref`` to a target that is
    being read is a cycle, and sets no type. The branches of an ``anyOf`` or
    ``oneOf`` are read until the first one that sets no type.
    """

    def __init__(self, root: object) -> None:
        self._root = root
        self._reading: list[str] = [""]
        self._read: dict[str, list[str] | None] = {}

    def of(self, schema: object) -> list[str] | None:
        if not isinstance(schema, Mapping):
            return None
        if "$ref" in schema:
            return self._of_ref(schema["$ref"])
        type_names = schema.get("type")
        types: set[str] | None = None
        if isinstance(type_names, str):
            types = {type_names}
        elif isinstance(type_names, list | tuple) and type_names:
            types = {name if isinstance(name, str) else "" for name in type_names}
        for keyword in ("anyOf", "oneOf"):
            branches = schema.get(keyword)
            if not isinstance(branches, list | tuple) or not branches:
                continue
            union: set[str] = set()
            for branch in branches:
                found = self.of(branch)
                if found is None:
                    break
                union.update(found)
            else:
                types = union if types is None else types & union
        return None if types is None else sorted(types)

    def _of_ref(self, reference: object) -> list[str] | None:
        if not isinstance(reference, str) or not reference.startswith("#"):
            return None
        pointer = reference[1:]
        if pointer in self._reading or len(self._reading) > _MAX_REF_DEPTH:
            return None
        if pointer in self._read:
            return self._read[pointer]
        target = _resolve_pointer(self._root, pointer)
        self._reading.append(pointer)
        try:
            types = None if target is _MISSING else self.of(target)
        finally:
            self._reading.pop()
        self._read[pointer] = types
        return types


def _resolve_pointer(root: object, pointer: str) -> object:
    """The value at a JSON Pointer in ``root``, or ``_MISSING``."""
    if not pointer:
        return root
    if not pointer.startswith("/"):
        return _MISSING
    node = root
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping) and token in node:
            node = node[token]
        elif (
            isinstance(node, Sequence)
            and not isinstance(node, str)
            and _ARRAY_INDEX.fullmatch(token)
            and int(token) < len(node)
        ):
            node = node[int(token)]
        else:
            return _MISSING
    return node


__all__ = [
    "TOOL_SCHEMA_NOT_OBJECT",
    "ToolsLeftOutSignal",
    "taking_objects_only",
    "type_is_not_object",
]


# The keywords whose values are maps of names to schemas: a name there is not a
# keyword.
_SCHEMA_MAPS = frozenset({
    "properties",
    "patternProperties",
    "definitions",
    "$defs",
    "dependencies",
})
# The keywords whose values are data, not schemas: a ``$ref`` there is a value.
_DATA_KEYWORDS = frozenset({"const", "enum", "default", "examples"})


def taking_objects_only(schema: JsonValue) -> JsonValue:
    """``schema`` restricted to objects, so Core registers it.

    It accepts the same objects as ``schema``, which sits under ``allOf``. Its
    local references (``#`` and ``#/…``) move there with it: ``#/$defs/Name``
    becomes ``#/allOf/0/$defs/Name``, so each one finds its target. Its
    ``$schema`` stays at the root, where a validator reads the dialect: under
    ``allOf`` it would be ignored, and the rest read in another dialect.
    """
    if isinstance(schema, dict) and "$schema" in schema:
        rest = {
            keyword: value for keyword, value in schema.items() if keyword != "$schema"
        }
        return {
            "$schema": schema["$schema"],
            "type": "object",
            "allOf": [_with_local_refs_under(rest, "/allOf/0")],
        }
    return {"type": "object", "allOf": [_with_local_refs_under(schema, "/allOf/0")]}


def _with_local_refs_under(
    schema: JsonValue, pointer: str, *, names: bool = False
) -> JsonValue:
    """``schema`` with each local ``$ref`` in it moved under ``pointer``.

    ``names`` says that ``schema`` maps names to schemas, such as the value of
    ``properties``. A subschema with its own ``$id`` is a document of its own,
    against which its references resolve, so it is kept as it is. Next to a
    ``$ref``, draft 7 ignores ``$id`` as any other keyword, so that ``$ref``
    resolves against the outer document and moves.
    """
    if isinstance(schema, list):
        return [_with_local_refs_under(item, pointer) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if names:
        return {
            name: _with_local_refs_under(value, pointer)
            for name, value in schema.items()
        }
    own_id = schema.get("$id")
    if isinstance(own_id, str) and not own_id.startswith("#") and "$ref" not in schema:
        return schema
    moved: dict[str, JsonValue] = {}
    for keyword, value in schema.items():
        if (
            keyword == "$ref"
            and isinstance(value, str)
            and (value == "#" or value.startswith("#/"))
        ):
            moved[keyword] = f"#{pointer}{value[1:]}"
        elif keyword in _DATA_KEYWORDS:
            moved[keyword] = value
        else:
            moved[keyword] = _with_local_refs_under(
                value, pointer, names=keyword in _SCHEMA_MAPS
            )
    return moved
