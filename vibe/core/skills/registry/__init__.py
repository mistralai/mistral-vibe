from __future__ import annotations

from vibe.core.skills.registry._client import RegistrySkillsError
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest
from vibe.core.skills.registry._service import (
    CatalogItem,
    RegistrySyncResult,
    RegistrySyncStatus,
    SkillDetails,
    SkillUpdate,
    check_new_versions,
    check_updates,
    convert_skill_to_local,
    get_skill_body,
    get_skill_details,
    has_registry_endpoint,
    import_skill,
    list_catalog,
    list_skill_versions,
    project_scope_available,
    refresh_registry_skills,
    remove_skill,
    set_skill_alias,
    set_skill_latest,
    set_skill_version,
)
from vibe.core.skills.registry.models import SkillVersionInfo

__all__ = [
    "CatalogItem",
    "ManifestEntry",
    "RegistrySkillsError",
    "RegistrySyncResult",
    "RegistrySyncStatus",
    "SkillDetails",
    "SkillManifest",
    "SkillUpdate",
    "SkillVersionInfo",
    "check_new_versions",
    "check_updates",
    "convert_skill_to_local",
    "get_skill_body",
    "get_skill_details",
    "has_registry_endpoint",
    "import_skill",
    "list_catalog",
    "list_skill_versions",
    "project_scope_available",
    "refresh_registry_skills",
    "remove_skill",
    "set_skill_alias",
    "set_skill_latest",
    "set_skill_version",
]
