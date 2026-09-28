mod prompt;
mod tools;

use std::collections::HashSet;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::helpers::normalized_similarity_score;

pub(crate) use prompt::{
    AUTONOMY_ACTIVITY, IDENTITY_GUIDANCE, catalog_prompt_section, rules_prompt_section,
};
pub(crate) use tools::{ToolName, direct_tool, is_direct_name, tool};

pub(crate) const NAMESPACE: &str = "skill";
const MAX_SKILL_NAME_SUGGESTIONS: usize = 3;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct SkillDefinition {
    pub name: String,
    pub description: String,
    pub path: String,
}

pub(crate) fn validate<'a>(
    skills: impl IntoIterator<Item = &'a SkillDefinition>,
) -> Result<(), String> {
    let mut names = HashSet::new();
    let mut paths = HashSet::new();
    for skill in skills {
        if !is_skill_name(&skill.name) {
            return Err(format!(
                "skill name {:?} must be an Agent Skill name or a Runtime-qualified namespace:name alias",
                skill.name
            ));
        }
        if skill.description.trim().is_empty() || skill.path.trim().is_empty() {
            return Err("skill descriptions and paths must not be empty".to_string());
        }
        if !is_skill_path(&skill.path) {
            return Err(format!(
                "skill path {:?} must point to SKILL.md",
                skill.path
            ));
        }
        if !names.insert(skill.name.clone()) {
            return Err(format!("duplicate skill name {:?}", skill.name));
        }
        if !paths.insert(skill.path.clone()) {
            return Err(format!("duplicate skill path {:?}", skill.path));
        }
    }
    Ok(())
}

pub(crate) fn validate_request<'a>(
    skills: impl IntoIterator<Item = &'a SkillDefinition>,
    arguments: &Value,
) -> Result<(), String> {
    let requested_name = arguments
        .get("name")
        .and_then(Value::as_str)
        .filter(|name| !name.trim().is_empty())
        .ok_or_else(|| "skill name must be a non-empty string".to_string())?;
    let available = skills
        .into_iter()
        .map(|skill| skill.name.as_str())
        .collect::<Vec<_>>();
    if available.contains(&requested_name) {
        return Ok(());
    }
    let suggestions = find_closest_skill_names(requested_name, &available);
    let suggestion_text = if suggestions.is_empty() {
        String::new()
    } else {
        format!(
            " Did you mean: {}?",
            suggestions
                .into_iter()
                .map(|name| format!("{name:?}"))
                .collect::<Vec<_>>()
                .join(", ")
        )
    };
    let available_text = if available.is_empty() {
        " No skills are available.".to_string()
    } else {
        format!(
            " Available skills: {}.",
            available
                .into_iter()
                .map(|name| format!("{name:?}"))
                .collect::<Vec<_>>()
                .join(", ")
        )
    };
    Err(format!(
        "Skill {requested_name:?} is not available.{suggestion_text} Use an exact name from the available skills below. Connector names and unlisted names in skill instructions are not skills.{available_text}"
    ))
}

fn find_closest_skill_names<'a>(requested_name: &str, available: &[&'a str]) -> Vec<&'a str> {
    let mut scored = available
        .iter()
        .copied()
        .map(|name| (name, normalized_similarity_score(requested_name, name)))
        .filter(|(_, score)| *score > 0.0)
        .collect::<Vec<_>>();
    scored.sort_by(|(_, left), (_, right)| right.total_cmp(left));
    scored
        .into_iter()
        .take(MAX_SKILL_NAME_SUGGESTIONS)
        .map(|(name, _)| name)
        .collect()
}

fn is_skill_name(value: &str) -> bool {
    if let Some((namespace, skill_name)) = value.split_once(':') {
        return !skill_name.contains(':')
            && is_programmatic_identifier(namespace)
            && is_unqualified_skill_name(skill_name);
    }
    is_unqualified_skill_name(value)
}

fn is_unqualified_skill_name(value: &str) -> bool {
    !value.is_empty()
        && value.split('-').all(|segment| {
            !segment.is_empty()
                && segment
                    .chars()
                    .all(|character| character.is_ascii_alphabetic() || character.is_ascii_digit())
        })
}

fn is_skill_path(value: &str) -> bool {
    (value.ends_with("/SKILL.md") || value.ends_with("\\SKILL.md"))
        && (value.starts_with('/')
            || value.starts_with("\\\\")
            || (value.len() >= 3
                && value.as_bytes()[0].is_ascii_alphabetic()
                && value.as_bytes()[1] == b':'
                && matches!(value.as_bytes()[2], b'/' | b'\\')))
}

fn is_programmatic_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    chars.next().is_some_and(|character| {
        character == '_' || character == '$' || character.is_ascii_alphabetic()
    }) && chars
        .all(|character| character == '_' || character == '$' || character.is_ascii_alphanumeric())
}
