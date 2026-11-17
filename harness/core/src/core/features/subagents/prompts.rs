use serde::{Deserialize, Serialize};

use crate::core::error::CoreError;
use crate::core::features::subagents::AgentTypeDefinition;

/// Agent tool text. Core's defaults live in `default_prompts.json`.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Prompts {
    /// Opens the agents section for an agent that can create children.
    pub guidance_intro: String,
    /// Follows `guidance_intro` only when the agent can call `list_configs`.
    pub list_configs_guidance: String,
    /// Introduces the list of agent types the agent can start with `config_name`.
    pub config_catalog: String,
    /// Ends that list when it leaves agent types out.
    pub config_catalog_more: String,
    /// Closes the agents section for an agent that can create children.
    pub guidance: String,
    /// The agents section for an agent that cannot create children.
    pub messaging_guidance: String,
    /// Describes `tools.agents` in tool search for an agent that can create children.
    pub search_description: String,
    /// Describes `tools.agents` in tool search for an agent that can only send messages.
    pub messaging_search_description: String,
    pub list_configs: String,
    pub list: String,
    pub spawn: String,
    pub spawn_with_conversation: String,
    pub send_message: String,
    pub interrupt: String,
}

impl Prompts {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if [
            &self.guidance_intro,
            &self.list_configs_guidance,
            &self.config_catalog,
            &self.config_catalog_more,
            &self.guidance,
            &self.messaging_guidance,
            &self.search_description,
            &self.messaging_search_description,
            &self.list_configs,
            &self.list,
            &self.spawn,
            &self.spawn_with_conversation,
            &self.send_message,
            &self.interrupt,
        ]
        .iter()
        .any(|value| value.trim().is_empty())
        {
            return Err(CoreError::invalid_configuration(
                "settings.prompts.agent_tools",
                "agent prompts must not be empty",
            ));
        }
        Ok(())
    }
}

/// Agent types listed in the system prompt; `list_configs` returns every one.
const MAX_LISTED_AGENT_TYPES: usize = 30;
/// Characters of a description listed in the system prompt; `list_configs` returns it whole.
const MAX_LISTED_DESCRIPTION_CHARS: usize = 200;
/// Characters of a name listed in the system prompt; a longer name is left out, not cut.
const MAX_LISTED_NAME_CHARS: usize = 100;

/// The agent types an agent can start with `config_name`, listed in its system prompt: at most
/// `MAX_LISTED_AGENT_TYPES`, each name exact and each description on one line and at most
/// `MAX_LISTED_DESCRIPTION_CHARS` long. A name with a line break or longer than
/// `MAX_LISTED_NAME_CHARS` cannot be listed exactly on one bounded line, so it is left out like
/// the rest; `more` ends the list when any is.
pub(crate) fn catalog_prompt_section<'a>(
    intro: &str,
    more: &str,
    agent_types: impl Iterator<Item = &'a AgentTypeDefinition>,
) -> Option<String> {
    let agent_types = agent_types.collect::<Vec<_>>();
    if agent_types.is_empty() {
        return None;
    }
    let listable = agent_types
        .iter()
        .filter(|agent_type| {
            !agent_type.name.contains(['\n', '\r'])
                && agent_type.name.chars().count() <= MAX_LISTED_NAME_CHARS
        })
        .collect::<Vec<_>>();
    let mut lines = listable
        .iter()
        .take(MAX_LISTED_AGENT_TYPES)
        .map(|agent_type| {
            format!(
                "- {}: {}",
                listed_name(&agent_type.name),
                listed_description(&agent_type.description)
            )
        })
        .collect::<Vec<_>>();
    if lines.len() < agent_types.len() {
        lines.push(more.to_string());
    }
    if lines.is_empty() {
        return None;
    }
    Some(format!("{intro}\n{}", lines.join("\n")))
}

fn listed_name(name: &str) -> String {
    let longest_run = name
        .split(|character| character != '`')
        .map(str::len)
        .max()
        .unwrap_or(0);
    let fence = "`".repeat(longest_run + 1);
    let padding = if name.starts_with(['`', ' ']) || name.ends_with(['`', ' ']) {
        " "
    } else {
        ""
    };
    format!("{fence}{padding}{name}{padding}{fence}")
}

fn listed_description(description: &str) -> String {
    let flat = description.split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.chars().count() <= MAX_LISTED_DESCRIPTION_CHARS {
        return flat;
    }
    let cut = flat
        .char_indices()
        .nth(MAX_LISTED_DESCRIPTION_CHARS - 1)
        .map_or(flat.len(), |(index, _)| index);
    let head = &flat[..cut];
    let head = head.rsplit_once(' ').map_or(head, |(words, _)| words);
    format!("{}…", head.trim_end_matches([',', ';', ':', '.']))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn agent_type(name: &str, description: &str) -> AgentTypeDefinition {
        AgentTypeDefinition {
            name: name.to_string(),
            description: description.to_string(),
            path: String::new(),
        }
    }

    /// *Assert*: A description is listed on one line, and one past the limit is cut at a word
    /// with an ellipsis.
    #[test]
    fn catalog_flattens_and_shortens_descriptions() {
        let long = format!("{} tail", "word ".repeat(60));
        let types = [
            agent_type("short", "Looks up\n  prices."),
            agent_type("long", &long),
        ];

        let section = catalog_prompt_section("Configs:", "- More.", types.iter())
            .expect("a catalog with agent types");

        assert!(section.contains("- `short`: Looks up prices.\n"));
        let odd = [
            agent_type("research  assistant", "Does work."),
            agent_type("odd`name", "Does work."),
            agent_type("two\nlines", "Does work."),
        ];
        let odd_section = catalog_prompt_section("Configs:", "- More.", odd.iter())
            .expect("a catalog with agent types");
        assert!(odd_section.contains("\n- `research  assistant`: Does work.\n"));
        assert!(odd_section.contains("\n- ``odd`name``: Does work.\n"));
        assert!(!odd_section.contains("two"));
        assert!(odd_section.ends_with("\n- More."));
        let long_line = section
            .lines()
            .find(|line| line.starts_with("- `long`"))
            .expect("the long agent type is listed");
        let listed = long_line.trim_start_matches("- `long`: ");
        assert!(listed.ends_with("word…"));
        assert!(listed.chars().count() <= MAX_LISTED_DESCRIPTION_CHARS);
    }

    /// *Assert*: A name longer than the limit is left out whole, and one at the limit is listed.
    #[test]
    fn catalog_leaves_out_names_longer_than_the_limit() {
        let at_limit = "é".repeat(MAX_LISTED_NAME_CHARS);
        let too_long = "a".repeat(MAX_LISTED_NAME_CHARS + 1);
        let types = [
            agent_type(&at_limit, "Does work."),
            agent_type(&too_long, "Does work."),
        ];

        let section = catalog_prompt_section("Configs:", "- More.", types.iter())
            .expect("a catalog with agent types");

        assert!(section.contains(&format!("\n- `{at_limit}`: Does work.\n")));
        assert!(!section.contains(&too_long));
        assert!(section.ends_with("\n- More."));
    }

    /// *Assert*: Past the listing limit, the catalog says it leaves configs out.
    #[test]
    fn catalog_lists_at_most_the_limit_and_says_when_it_leaves_some_out() {
        let types = (0..MAX_LISTED_AGENT_TYPES + 3)
            .map(|index| agent_type(&format!("agent-{index:02}"), "Does work."))
            .collect::<Vec<_>>();

        let section = catalog_prompt_section("Configs:", "- More.", types.iter())
            .expect("a catalog with agent types");

        assert_eq!(
            section
                .lines()
                .filter(|line| line.starts_with("- `"))
                .count(),
            MAX_LISTED_AGENT_TYPES
        );
        assert!(section.ends_with("\n- More."));
        assert!(catalog_prompt_section("Configs:", "- More.", std::iter::empty()).is_none());
    }
}
