from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConfigSchemaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str
    config_schema: dict[str, Any] = Field(alias="schema")
