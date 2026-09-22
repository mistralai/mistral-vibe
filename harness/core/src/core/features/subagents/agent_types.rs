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

pub(crate) fn render_agent_types<'a>(
    agent_types: impl Iterator<Item = &'a AgentTypeDefinition>,
) -> Option<String> {
    let mut agent_types = agent_types.collect::<Vec<_>>();
    if agent_types.is_empty() {
        return None;
    }

    agent_types.sort_by(|left, right| left.name.cmp(&right.name));
    let entries = agent_types
        .into_iter()
        .map(|agent_type| {
            format!(
                r#"  <agent>
    <name>{}</name>
    <description>{}</description>
    <path>{}</path>
  </agent>"#,
                escape_xml(&agent_type.name),
                escape_xml(&agent_type.description),
                escape_xml(&agent_type.path),
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    Some(format!(
        r#"## Available agent types

Spawn a listed preconfigured subagent by passing its exact name as `agentType` to `tools.agent.spawn`.

<available-agent-types>
{entries}
</available-agent-types>"#
    ))
}

fn validate_text(value: &str, field: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        return Err(format!("{field} must not be empty"));
    }
    Ok(())
}

fn escape_xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}
