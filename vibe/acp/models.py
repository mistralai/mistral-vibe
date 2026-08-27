from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConfigSchemaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str
    config_schema: dict[str, Any] = Field(alias="schema")


# -- Project links ------------------------------------------------------------
#
# Request params for the projectLinks/* ACP ext methods. Every method is
# stateless and takes the absolute `rootPath` held by desktop-main; the
# app-server ProjectLinksController intentionally returns `repoLocalPath`;
# renderers that need compact labels should derive them from the basename.


class ProjectLinksListRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ProjectLinksResolveRootRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)


class ProjectLinksPickerLoadRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)


class ProjectLinksPickerLoadMoreRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)
    cursor: str = Field(min_length=1)


class ProjectLinksCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)
    name: str = Field(min_length=1)
    default_branch: str = Field(alias="defaultBranch", min_length=1)


class ProjectLinksLinkRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)
    project_id: str = Field(alias="projectId", min_length=1)
    project_name: str = Field(alias="projectName", min_length=1)


class ProjectLinksUnlinkRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)
