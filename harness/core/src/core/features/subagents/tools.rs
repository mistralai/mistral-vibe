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

    fn spec(self) -> ToolSpec {
        let (description, input_schema, output_schema) = match self {
            Self::ListConfigs => (
                "List available named configurations, sorted by name. Omit config_name when spawning a generic child that inherits this agent's configuration.",
                ListInput::tool_schema(),
                ConfigsOutput::tool_schema(),
            ),
            Self::List => (
                "List admitted direct children in creation order. Provisional spawns are not listed.",
                ListInput::tool_schema(),
                ListOutput::tool_schema(),
            ),
            Self::Spawn => (
                "Create an independent child and admit its first message, without waiting for completion. name is a single path segment unique for the lifetime of this parent Session and cannot be reused. Omit config_name to inherit this agent's configuration.",
                SpawnInput::tool_schema(),
                OperationResult::tool_schema(),
            ),
            Self::SendMessage => (
                "Send a message to an agent path. Admission does not imply consumption or a reply.",
                MessageInput::tool_schema(),
                OperationResult::tool_schema(),
            ),
            Self::Interrupt => (
                "Interrupt a direct child's exact current Turn without closing its Session.",
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
    pub(crate) description: &'static str,
    pub(crate) input_schema: Value,
    pub(crate) output_schema: Value,
}

pub(crate) fn tools() -> Vec<ToolSpec> {
    ToolName::ALL.into_iter().map(ToolName::spec).collect()
}
