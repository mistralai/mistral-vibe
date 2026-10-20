#![expect(dead_code, reason = "private contract types are reflected by Schemars")]

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::tools::schema::ToolSchema;

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ListInput {}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct SpawnInput {
    #[schemars(length(min = 1), regex(pattern = r"^(?!\.{1,2}$)[^/]+$"))]
    name: String,
    #[schemars(length(min = 1))]
    message: String,
    #[schemars(length(min = 1))]
    config_name: Option<String>,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ConversationSpawnInput {
    #[schemars(flatten)]
    spawn: SpawnInput,
    #[schemars(
        default = "default_fork_turns",
        regex(pattern = r"^(all|none|0*[1-9][0-9]*)$")
    )]
    fork_turns: String,
}

fn default_fork_turns() -> String {
    "none".into()
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct MessageInput {
    #[schemars(length(min = 1))]
    agent_path: String,
    #[schemars(length(min = 1))]
    message: String,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct AgentInput {
    #[schemars(length(min = 1))]
    name: String,
}

#[derive(JsonSchema)]
#[schemars(tag = "status", rename_all = "lowercase", deny_unknown_fields)]
enum ListedAgent {
    Running {
        name: String,
        config_name: Option<String>,
        #[schemars(extend("const" = true))]
        pending_turn: bool,
    },
    Idle {
        name: String,
        config_name: Option<String>,
        #[schemars(extend("const" = false))]
        pending_turn: bool,
    },
    Failed {
        name: String,
        config_name: Option<String>,
        #[schemars(extend("const" = false))]
        pending_turn: bool,
        error: String,
    },
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ListOutput {
    agents: Vec<ListedAgent>,
}

#[derive(JsonSchema)]
struct NamedConfiguration {
    name: String,
    description: String,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ConfigsOutput {
    configs: Vec<NamedConfiguration>,
}

#[derive(JsonSchema)]
#[schemars(tag = "type", rename_all = "lowercase", deny_unknown_fields)]
enum OperationResult {
    Success,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, Eq, Hash, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ToolName {
    ListConfigs,
    List,
    Spawn,
    SendMessage,
    Interrupt,
}

impl ToolName {
    pub(crate) const ALL: [Self; 5] = [
        Self::ListConfigs,
        Self::List,
        Self::Spawn,
        Self::SendMessage,
        Self::Interrupt,
    ];

    pub(crate) fn runtime_tool(self) -> RuntimeBuiltinToolName {
        match self {
            Self::ListConfigs => RuntimeBuiltinToolName::SubagentListConfigs,
            Self::List => RuntimeBuiltinToolName::SubagentList,
            Self::Spawn => RuntimeBuiltinToolName::SubagentSpawn,
            Self::SendMessage => RuntimeBuiltinToolName::SubagentSendMessage,
            Self::Interrupt => RuntimeBuiltinToolName::SubagentInterrupt,
        }
    }

    pub(crate) fn sandbox_name(self) -> &'static str {
        match self {
            Self::ListConfigs => "agents_list_configs",
            Self::List => "agents_list",
            Self::Spawn => "agents_spawn",
            Self::SendMessage => "agents_send_message",
            Self::Interrupt => "agents_interrupt",
        }
    }

    pub(crate) fn function_name(self) -> &'static str {
        match self {
            Self::ListConfigs => "list_configs",
            Self::List => "list",
            Self::Spawn => "spawn",
            Self::SendMessage => "send_message",
            Self::Interrupt => "interrupt",
        }
    }

    fn spec(self, conversation_forking: bool, prompts: &super::Prompts) -> ToolSpec {
        let (description, input_schema, output_schema) = match self {
            Self::ListConfigs => (
                prompts.list_configs.clone(),
                ListInput::tool_schema(),
                ConfigsOutput::tool_schema(),
            ),
            Self::List => (
                prompts.list.clone(),
                ListInput::tool_schema(),
                ListOutput::tool_schema(),
            ),
            Self::Spawn if conversation_forking => (
                prompts.spawn_with_conversation.clone(),
                ConversationSpawnInput::tool_schema(),
                OperationResult::tool_schema(),
            ),
            Self::Spawn => (
                prompts.spawn.clone(),
                SpawnInput::tool_schema(),
                OperationResult::tool_schema(),
            ),
            Self::SendMessage => (
                prompts.send_message.clone(),
                MessageInput::tool_schema(),
                OperationResult::tool_schema(),
            ),
            Self::Interrupt => (
                prompts.interrupt.clone(),
                AgentInput::tool_schema(),
                OperationResult::tool_schema(),
            ),
        };
        ToolSpec {
            name: self,
            description,
            input_schema,
            output_schema,
        }
    }
}

pub(crate) struct ToolSpec {
    pub(crate) name: ToolName,
    pub(crate) description: String,
    pub(crate) input_schema: Value,
    pub(crate) output_schema: Value,
}

pub(crate) fn tools(conversation_forking: bool, prompts: &super::Prompts) -> Vec<ToolSpec> {
    ToolName::ALL
        .into_iter()
        .map(|name| name.spec(conversation_forking, prompts))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn omitted_fork_turns_advertises_a_fresh_child_conversation() {
        let spawn = tools(
            true,
            &crate::core::prompt::PromptSettings::default().agent_tools,
        )
        .into_iter()
        .find(|spec| matches!(spec.name, ToolName::Spawn))
        .unwrap();

        assert_eq!(
            spawn.input_schema["properties"]["fork_turns"]["default"],
            "none"
        );
        assert!(spawn.description.contains("none (default"));
    }
}
