from __future__ import annotations

import base64
import binascii
import re

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

_NON_NAME_CHARS = re.compile(r"[^a-z0-9]+")
# Mirror SkillMetadata.name's max_length so a sanitized name always validates.
_MAX_NAME_LEN = 64


def sanitize_skill_name(raw: str) -> str | None:
    collapsed = _NON_NAME_CHARS.sub("-", raw.strip().lower()).strip("-")
    collapsed = collapsed[:_MAX_NAME_LEN].rstrip("-")
    return collapsed or None


class RegistryAssetContent(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    text_content: str | None = Field(
        default=None, validation_alias=AliasChoices("textContent", "text_content")
    )
    raw_content: str | None = Field(
        default=None, validation_alias=AliasChoices("rawContent", "raw_content")
    )
    is_executable: bool = Field(
        default=False, validation_alias=AliasChoices("isExecutable", "is_executable")
    )

    def to_bytes(self) -> bytes | None:
        if self.text_content is not None:
            return self.text_content.encode("utf-8")
        if self.raw_content is not None:
            try:
                return base64.b64decode(self.raw_content, validate=True)
            except (binascii.Error, ValueError):
                return None
        return None


class RegistrySkillPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    skill_name: str = Field(
        default="", validation_alias=AliasChoices("skillName", "skill_name")
    )
    skill_description: str = Field(
        default="",
        validation_alias=AliasChoices("skillDescription", "skill_description"),
    )
    skill_body: str = Field(
        default="", validation_alias=AliasChoices("skillBody", "skill_body")
    )
    skill_assets: dict[str, RegistryAssetContent] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("skillAssets", "skill_assets"),
    )


class RegistryAttributes(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    title: str = ""
    description: str | None = None


class RegistryMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = ""
    latest_version: int = Field(
        default=0, validation_alias=AliasChoices("latestVersion", "latest_version")
    )
    sharing_scope: str = Field(
        default="", validation_alias=AliasChoices("sharingScope", "sharing_scope")
    )
    created_at: str = Field(
        default="", validation_alias=AliasChoices("createdAt", "created_at")
    )
    last_modified_at: str = Field(
        default="", validation_alias=AliasChoices("lastModifiedAt", "last_modified_at")
    )
    created_by: str = Field(
        default="", validation_alias=AliasChoices("createdBy", "created_by")
    )


class RegistryVersionMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    created_at: str = Field(
        default="", validation_alias=AliasChoices("createdAt", "created_at")
    )


class RegistryVersionAttributes(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    notes: str = ""
    aliases: list[str] = Field(default_factory=list)


class RegistrySkillItem(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    skill_id: str = Field(
        default="", validation_alias=AliasChoices("skillId", "skill_id")
    )
    skill: RegistrySkillPayload = Field(default_factory=RegistrySkillPayload)
    attributes: RegistryAttributes = Field(default_factory=RegistryAttributes)
    metadata: RegistryMetadata = Field(default_factory=RegistryMetadata)
    version: int = 0
    version_metadata: RegistryVersionMetadata = Field(
        default_factory=RegistryVersionMetadata,
        validation_alias=AliasChoices("versionMetadata", "version_metadata"),
    )
    version_attributes: RegistryVersionAttributes = Field(
        default_factory=RegistryVersionAttributes,
        validation_alias=AliasChoices("versionAttributes", "version_attributes"),
    )

    @property
    def resolved_name(self) -> str | None:
        for candidate in (
            self.metadata.name,
            self.skill.skill_name,
            self.attributes.title,
        ):
            if (name := sanitize_skill_name(candidate)) is not None:
                return name
        if self.skill_id:
            # Run the id-based fallback through the same sanitizer so it obeys
            # the charset / length / hyphen rules SkillMetadata.name enforces.
            # The full id (not a prefix) keeps unnamed skills collision-free.
            return sanitize_skill_name(f"skill-{self.skill_id.replace('-', '')}")
        return None

    @property
    def resolved_description(self) -> str:
        if self.skill.skill_description.strip():
            return self.skill.skill_description.strip()
        if self.attributes.description and self.attributes.description.strip():
            return self.attributes.description.strip()
        return ""


class ListSkillsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    data: list[RegistrySkillItem] = Field(default_factory=list)
    next_page_token: str = Field(
        default="", validation_alias=AliasChoices("nextPageToken", "next_page_token")
    )


class SkillVersionInfo(BaseModel):
    """One row from the versions endpoint: a concrete version and its author
    aliases (e.g. 'main', 'stable'). The reserved 'latest' alias is dynamic and
    not part of this list.
    """

    version: int
    aliases: list[str] = Field(default_factory=list)


class RegistryVersionRow(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: int
    version_attributes: RegistryVersionAttributes = Field(
        default_factory=RegistryVersionAttributes,
        validation_alias=AliasChoices("versionAttributes", "version_attributes"),
    )

    def to_info(self) -> SkillVersionInfo:
        return SkillVersionInfo(
            version=self.version, aliases=list(self.version_attributes.aliases)
        )


class ListVersionsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    items: list[RegistryVersionRow] = Field(default_factory=list)
