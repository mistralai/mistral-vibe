from __future__ import annotations

import hashlib
from typing import Annotated
import unicodedata

from pydantic import BaseModel, BeforeValidator, JsonValue
import rfc8785


class PluginEncodingError(ValueError):
    def __init__(self, value: str) -> None:
        super().__init__("plugin string is not well-formed NFC Unicode")
        self.value = value


def normalize_nfc(value: str) -> str:
    try:
        value.encode()
    except UnicodeEncodeError as error:
        raise PluginEncodingError(value) from error
    return unicodedata.normalize("NFC", value)


def normalize_json(value: JsonValue) -> JsonValue:
    match value:
        case str():
            return normalize_nfc(value)
        case dict():
            return {
                normalize_nfc(key): normalize_json(item) for key, item in value.items()
            }
        case list():
            return [normalize_json(item) for item in value]
        case _:
            return value


NormalizedStr = Annotated[str, BeforeValidator(normalize_nfc)]
NormalizedJson = Annotated[JsonValue, BeforeValidator(normalize_json)]


def canonical_json(value: JsonValue) -> bytes:
    return rfc8785.dumps(value)


def canonical_json_digest(value: JsonValue | BaseModel) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return hashlib.sha256(canonical_json(payload)).hexdigest()
