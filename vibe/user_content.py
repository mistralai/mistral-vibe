from __future__ import annotations

from typing import Annotated, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
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
    media_type: str | None = None


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


UserResource = Annotated[
    UserTextResource | UserBlobResource | UserResourceLink, Field(discriminator="kind")
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
