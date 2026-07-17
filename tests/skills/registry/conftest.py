from __future__ import annotations

from typing import Any

from vibe.core.skills.registry.models import RegistrySkillItem


def make_item(
    *,
    skill_id: str = "id-1",
    name: str | None = "my_skill",
    skill_name: str = "Free Form",
    description: str = "does things",
    body: str = "# Body\n\ninstructions",
    assets: dict[str, Any] | None = None,
    version: int = 1,
    latest_version: int | None = None,
) -> RegistrySkillItem:
    payload: dict[str, Any] = {
        "skillId": skill_id,
        "skill": {
            "skillName": skill_name,
            "skillDescription": description,
            "skillBody": body,
            "skillAssets": assets or {},
        },
        "version": version,
    }
    metadata: dict[str, Any] = {}
    if name is not None:
        metadata["name"] = name
    if latest_version is not None:
        metadata["latestVersion"] = latest_version
    if metadata:
        payload["metadata"] = metadata
    return RegistrySkillItem.model_validate(payload)
