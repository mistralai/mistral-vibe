//! The shared skills `/skills` lists, sorted by name and indexed by skill id together.

use std::collections::HashMap;

use caseless::default_case_fold_str as casefold;

use crate::server::{SkillCatalog, SkillCatalogEntry};

/// A `skills/catalog` answer sorted by name with each entry's position by skill id; only `new` builds one, so both stay in step.
#[derive(Debug, Default)]
pub struct Catalog {
    answer: SkillCatalog,
    index: HashMap<String, usize>,
}

impl Catalog {
    pub fn new(mut answer: SkillCatalog) -> Self {
        answer
            .skills
            .sort_by_cached_key(|entry| casefold(&entry.name));
        let index = answer
            .skills
            .iter()
            .enumerate()
            .map(|(position, entry)| (entry.skill_id.clone(), position))
            .collect();
        Self { answer, index }
    }

    pub fn entries(&self) -> &[SkillCatalogEntry] {
        &self.answer.skills
    }

    pub fn entry(&self, skill_id: &str) -> Option<&SkillCatalogEntry> {
        self.answer.skills.get(*self.index.get(skill_id)?)
    }

    /// The newest version of `name` when the frozen pin has an older one.
    pub fn update(&self, name: &str) -> Option<i64> {
        self.answer.updates.get(name).copied()
    }

    pub fn loaded(&self) -> bool {
        self.answer.loaded
    }

    pub fn project_available(&self) -> bool {
        self.answer.project_available
    }

    pub fn authenticated(&self) -> bool {
        self.answer.authenticated
    }
}
