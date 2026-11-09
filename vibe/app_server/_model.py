from __future__ import annotations

from collections.abc import Mapping

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


def validate_backend_wire[ModelT: ProtocolModel](
    model: type[ModelT], value: Mapping[str, object]
) -> ModelT:
    """Validate a payload *read from a backend*, ignoring unknown top-level fields.

    The client↔app-server wire contract is strict (``extra="forbid"``) because
    both sides ship together. A backend such as the Unified Harness runtime
    upgrades independently, so when we *read its output* we must tolerate it
    running ahead and adding fields (ADR 0014): unknown top-level keys are
    dropped before validation rather than rejected.

    Tolerance covers additive fields only. Known fields still validate, so a
    genuine type error is not swallowed, and a field the model requires (no
    default) that the backend dropped still fails — that is a real break, not
    additive evolution. Use this only for reading backend output; constructing
    input a backend consumes stays strict via :func:`validate_wire`.

    Only top-level keys are filtered — nested models still validate strictly, so
    a new *nested* field would need its own handling.
    """
    allowed = {field.alias or name for name, field in model.model_fields.items()}
    filtered = {key: item for key, item in value.items() if key in allowed}
    return model.model_validate(filtered, by_alias=True, by_name=False)
