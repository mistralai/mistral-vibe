from __future__ import annotations

from typing import Annotated, Literal, assert_never

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
)
from pydantic.alias_generators import to_camel

__all__ = [
    "UserBlobResource",
    "UserDisplayContent",
    "UserResource",
    "UserResourceLink",
    "UserTextResource",
    "render_user_resources",
]


class _UserResourceBase(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    uri: str = Field(min_length=1)
    # The public wire spells the media type ``mediaType``; the harness storage
    # format (rmcp) spells it ``mimeType``. Accept both when reading, keep the
    # public spelling on output.
    media_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mediaType", "mimeType"),
        serialization_alias="mediaType",
    )


class UserTextResource(_UserResourceBase):
    kind: Literal["text"] = "text"
    text: str


class UserBlobResource(_UserResourceBase):
    kind: Literal["blob"] = "blob"
    blob: str


class UserResourceLink(_UserResourceBase):
    kind: Literal["link"] = "link"
    name: str | None = None
    title: str | None = None
    description: str | None = None
    size: int | None = Field(default=None, ge=0)


def _infer_resource_kind(value: object) -> object:
    """Tolerate embedded resources stored without a ``kind`` tag.

    Sessions written by code that serializes the harness interop shape carry
    resource attachments as ``{"uri", "text"}`` (or ``blob``) with no ``kind``,
    which the discriminated union below cannot tag. Infer the variant from the
    payload keys so those sessions stay resumable — the same inference the
    legacy import path (``session_interop._import_content``) applies. A dict
    carrying neither ``text`` nor ``blob`` is left alone and still fails
    validation as genuine corruption.
    """
    if not isinstance(value, dict) or "kind" in value:
        return value
    if "text" in value:
        return {**value, "kind": "text"}
    if "blob" in value:
        return {**value, "kind": "blob"}
    return value


# The validator wraps the tagged union rather than sitting beside it: when
# this alias is used directly (a ``TypeAdapter`` over ``UserResource``, the
# path a legacy-session import takes), pydantic extracts the discriminator
# before applying sibling metadata in the same ``Annotated``, so a kindless
# dict never reaches a ``BeforeValidator`` placed next to
# ``Field(discriminator=...)``. Model-field use inside another model does not
# hit that ordering, but the wrapper is correct for both.
UserResource = Annotated[
    Annotated[
        UserTextResource | UserBlobResource | UserResourceLink,
        Field(discriminator="kind"),
    ],
    BeforeValidator(_infer_resource_kind),
]


def render_user_resources(resources: list[UserResource]) -> str:
    return "\n\n".join(_render_user_resource(resource) for resource in resources)


def _render_user_resource(resource: UserResource) -> str:
    match resource:
        case UserTextResource():
            return f"path: {resource.uri}\ncontent: {resource.text}"
        case UserBlobResource():
            return f"path: {resource.uri}\ncontent (base64): {resource.blob}"
        case UserResourceLink():
            fields = (
                ("uri", resource.uri),
                ("name", resource.name),
                ("title", resource.title),
                ("description", resource.description),
                ("mime_type", resource.media_type),
                ("size", resource.size),
            )
            return "\n".join(
                f"{name}: {value}" for name, value in fields if value is not None
            )
        case _:
            assert_never(resource)


class UserDisplayContent(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    version: str = Field(min_length=1)
    host: str = Field(min_length=1)
    content: list[dict[str, JsonValue]]

    @field_validator("version", "host")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped
