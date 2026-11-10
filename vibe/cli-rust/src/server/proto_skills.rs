//! Skills browser wire types (`vibe/app_server/models.py`).

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use super::method;

/// The registry skill a pin points at, the version it resolves to, and the alias it tracks if any.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillRegistryRef {
    #[serde(default)]
    pub skill_id: String,
    #[serde(default)]
    pub version: i64,
    #[serde(default)]
    pub alias: Option<String>,
}

impl SkillRegistryRef {
    /// The alias the pin tracks; an empty one tracks nothing.
    pub fn tracked_alias(&self) -> Option<&str> {
        self.alias.as_deref().filter(|alias| !alias.is_empty())
    }
}

/// One installed pin; `source` and `scope` stay strings so a new value still shows.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstalledSkill {
    pub name: String,
    #[serde(default)]
    pub description: String,
    /// The skill body, shown on its page.
    #[serde(default)]
    pub prompt: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub scope: String,
    #[serde(default)]
    pub registry: Option<SkillRegistryRef>,
    #[serde(default = "true_by_default")]
    pub enabled: bool,
    #[serde(default)]
    pub locked: bool,
    /// The pin the agent resolves the name to; a shadowed pin is still listed so it can be managed.
    #[serde(default = "true_by_default")]
    pub active: bool,
}

impl InstalledSkill {
    /// The registry ref of a registry pin.
    pub fn registry_ref(&self) -> Option<&SkillRegistryRef> {
        if self.source != "registry" {
            return None;
        }
        self.registry.as_ref()
    }

    /// The shared skill a registry pin points at.
    pub fn registry_id(&self) -> Option<&str> {
        self.registry_ref()
            .map(|registry| registry.skill_id.as_str())
    }

    pub fn in_scope(&self, scope: SkillScope) -> bool {
        self.scope == scope.as_str()
    }
}

/// A `skills/installed` answer, one entry per pin in server order.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct InstalledSkills {
    #[serde(default, deserialize_with = "crate::utils::json::lenient")]
    pub skills: Vec<InstalledSkill>,
}

/// A registry skill the session could import.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillCatalogEntry {
    pub name: String,
    pub skill_id: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub latest_version: i64,
    #[serde(default)]
    pub sharing_scope: String,
}

/// A `skills/catalog` answer: the shared skills, newer versions of frozen pins, and what the session can do.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillCatalog {
    #[serde(default, deserialize_with = "crate::utils::json::lenient")]
    pub skills: Vec<SkillCatalogEntry>,
    #[serde(default)]
    pub updates: BTreeMap<String, i64>,
    #[serde(default)]
    pub loaded: bool,
    #[serde(default)]
    pub project_available: bool,
    #[serde(default = "true_by_default")]
    pub authenticated: bool,
}

impl Default for SkillCatalog {
    fn default() -> Self {
        Self {
            skills: Vec::new(),
            updates: BTreeMap::new(),
            loaded: false,
            project_available: false,
            authenticated: true,
        }
    }
}

/// One published version of a registry skill and the aliases pointing at it.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct SkillVersion {
    pub version: i64,
    #[serde(default)]
    pub aliases: Vec<String>,
}

/// A `skills/versions` answer.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct SkillVersions {
    #[serde(default, deserialize_with = "crate::utils::json::lenient")]
    pub versions: Vec<SkillVersion>,
}

/// The body inside a `skills/detail` answer.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct SkillDetailView {
    #[serde(default)]
    pub body: String,
}

/// A `skills/detail` answer; `body` is the fallback when the detail failed.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct SkillDetail {
    #[serde(default)]
    pub detail: Option<SkillDetailView>,
    #[serde(default)]
    pub body: Option<String>,
}

impl SkillDetail {
    /// The body, `None` when the server could fetch neither the detail nor the fallback.
    pub fn into_text(self) -> Option<String> {
        match self.detail {
            Some(detail) => Some(detail.body),
            None => self.body,
        }
    }
}

/// A pinned skill with a version it has not announced yet.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct SkillUpdate {
    pub name: String,
}

/// A `skills/updates` answer.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct SkillUpdates {
    #[serde(default, deserialize_with = "crate::utils::json::lenient")]
    pub updates: Vec<SkillUpdate>,
}

/// A scope a registry pin can be written to.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum SkillScope {
    Global,
    Project,
}

impl SkillScope {
    pub const ALL: [Self; 2] = [Self::Global, Self::Project];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Global => "global",
            Self::Project => "project",
        }
    }
}

/// A change to the installed skills, sent as one `skills/*` mutation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(untagged, rename_all_fields = "camelCase")]
pub enum SkillChange {
    SetEnabled {
        name: String,
        enabled: bool,
    },
    Remove {
        name: String,
        scope: SkillScope,
    },
    /// Pins the skill in `scope`, replacing its own pin there; neither a version nor an alias means `latest`.
    Import {
        skill_id: String,
        version: Option<i64>,
        alias: Option<String>,
        scope: SkillScope,
    },
}

impl SkillChange {
    pub fn method(&self) -> &'static str {
        match self {
            Self::SetEnabled { .. } => method::SKILLS_SET_ENABLED,
            Self::Remove { .. } => method::SKILLS_REMOVE,
            Self::Import { .. } => method::SKILLS_IMPORT,
        }
    }
}

/// A missing flag that defaults to on.
fn true_by_default() -> bool {
    true
}
