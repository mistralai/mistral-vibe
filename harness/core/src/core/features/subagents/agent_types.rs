use std::collections::HashSet;

use serde::{Deserialize, Serialize};

const MAX_AGENT_TYPES: usize = 128;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct AgentTypeDefinition {
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) path: String,
}

pub(crate) fn validate_agent_types<'a>(
    agent_types: impl Iterator<Item = &'a AgentTypeDefinition>,
) -> Result<(), String> {
    let agent_types = agent_types.collect::<Vec<_>>();
    if agent_types.len() > MAX_AGENT_TYPES {
        return Err(format!(
            "configuration contains more than {MAX_AGENT_TYPES} agent types"
        ));
    }

    let mut names = HashSet::new();
    for agent_type in agent_types {
        validate_text(&agent_type.name, "agent type name")?;
        validate_text(&agent_type.description, "agent type description")?;
        validate_text(&agent_type.path, "agent type path")?;
        if !names.insert(agent_type.name.as_str()) {
            return Err(format!("duplicate agent type name {:?}", agent_type.name));
        }
    }
    Ok(())
}

fn validate_text(value: &str, field: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        return Err(format!("{field} must not be empty"));
    }
    Ok(())
}
