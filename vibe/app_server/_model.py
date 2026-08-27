from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ProtocolModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        defer_build=True,
    )


def validate_wire[ModelT: ProtocolModel](model: type[ModelT], value: object) -> ModelT:
    return model.model_validate(value, by_alias=True, by_name=False)
