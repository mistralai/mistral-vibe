//! User-invocable skills offered as `/name` commands, and where each was found.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Whom a skill applies to, read from the server's `scope`: how it was
/// installed (local file, plugin, registry) does not matter to the user, and a
/// plugin skill already shows its plugin in its `plugin:name`.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SkillOrigin {
    /// This project only (`scope: project`).
    Project,
    /// The user, in every project (`scope: global`).
    User,
    /// Shipped with Vibe (`source` or `scope` `builtin`).
    Vibe,
    /// Unknown to an older server, or a value this client does not know.
    #[default]
    #[serde(other)]
    Other,
}

impl SkillOrigin {
    /// Tolerant of older servers: a missing or unknown value is `Other`.
    pub fn from_wire(source: Option<&str>, scope: Option<&str>) -> Self {
        match (source, scope) {
            (Some("builtin"), _) | (_, Some("builtin")) => Self::Vibe,
            (_, Some("project")) => Self::Project,
            (_, Some("global")) => Self::User,
            _ => Self::Other,
        }
    }

    /// The label leading a skill's description in the `/` menu.
    pub fn label(self) -> &'static str {
        match self {
            Self::Other => "skill",
            Self::Project => "project",
            Self::User => "user",
            Self::Vibe => "vibe",
        }
    }
}

/// A skill entry; older caches stored only `[name, description]`.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Skill {
    pub name: String,
    pub description: String,
    #[serde(default)]
    pub origin: SkillOrigin,
}

impl Skill {
    pub fn new(name: impl Into<String>, description: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            description: description.into(),
            origin: SkillOrigin::Other,
        }
    }

    /// A runtime `SkillSummary`, or `None` when it is not user-invocable.
    pub fn from_wire(skill: &Value) -> Option<Self> {
        if skill.get("userInvocable").and_then(Value::as_bool) != Some(true) {
            return None;
        }
        let text = |key| skill.get(key).and_then(Value::as_str);
        Some(Self {
            name: text("name")?.to_owned(),
            description: text("description").unwrap_or("").to_owned(),
            origin: SkillOrigin::from_wire(text("source"), text("scope")),
        })
    }
}
