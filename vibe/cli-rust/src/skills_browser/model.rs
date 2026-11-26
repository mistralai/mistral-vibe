//! What a skill page is about, what each scope pins, and its bodies.

use std::collections::BTreeMap;

use crate::server::{InstalledSkill, SkillScope, SkillVersion};

/// The registry's reserved alias, always the newest version.
pub const LATEST: &str = "latest";

/// What a scope pins: an alias it tracks, or a fixed version.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Choice {
    Alias(String),
    Version(i64),
}

impl Choice {
    /// The choice an installed registry pin holds.
    pub fn of(pin: &InstalledSkill) -> Option<Self> {
        let registry = pin.registry_ref()?;
        Some(match registry.tracked_alias() {
            Some(alias) => Self::Alias(alias.to_owned()),
            None => Self::Version(registry.version),
        })
    }
}

/// The skill a page is about.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Subject {
    /// A local or plugin skill: a switch and a body.
    Plain { name: String },
    /// A shared skill, installed or not yet.
    Registry {
        name: String,
        skill_id: String,
        install: Install,
    },
}

/// A shared skill is installed, or each chosen scope waits for Install with its choice.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Install {
    Done,
    Pending(BTreeMap<SkillScope, Choice>),
}

impl Subject {
    pub fn name(&self) -> &str {
        match self {
            Self::Plain { name } | Self::Registry { name, .. } => name,
        }
    }

    pub fn skill_id(&self) -> Option<&str> {
        match self {
            Self::Registry { skill_id, .. } => Some(skill_id),
            Self::Plain { .. } => None,
        }
    }

    /// The choices waiting for Install, for a shared skill not installed yet.
    pub fn pending(&self) -> Option<&BTreeMap<SkillScope, Choice>> {
        match self {
            Self::Registry {
                install: Install::Pending(choices),
                ..
            } => Some(choices),
            _ => None,
        }
    }

    pub fn installed(&self) -> bool {
        self.pending().is_none()
    }
}

/// A skill body: loading, fetched, or why it could not be.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Body {
    Loading,
    Ready(String),
    Failed(String),
}

/// The version an alias points at today: the newest for `latest`, else the version listing it.
pub fn resolve(versions: &[SkillVersion], alias: &str) -> Option<i64> {
    if alias == LATEST {
        return versions.iter().map(|v| v.version).max();
    }
    let listed = |v: &&SkillVersion| v.aliases.iter().any(|a| a == alias);
    versions.iter().find(listed).map(|v| v.version)
}

/// The version a choice stands for today.
pub fn version_of(versions: &[SkillVersion], choice: &Choice) -> Option<i64> {
    match choice {
        Choice::Alias(alias) => resolve(versions, alias),
        Choice::Version(version) => Some(*version),
    }
}
