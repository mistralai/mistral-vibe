#![expect(
    dead_code,
    reason = "private contract types are reflected by Schemars rather than constructed"
)]

use schemars::JsonSchema;
use serde_json::Value;

use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::tools::schema::ToolSchema;

fn is_default<T: Default + PartialEq>(value: &T) -> bool {
    value == &T::default()
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ListInput {}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct SpawnInput {
    #[schemars(length(min = 1))]
    agent_name: String,
    #[schemars(length(min = 1))]
    message: String,
    #[schemars(default, skip_serializing_if = "is_default", length(min = 1))]
    agent_type: String,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct WaitInput {
    #[schemars(length(min = 1))]
    agent_name: String,
    #[schemars(range(min = 1))]
    timeout_ms: i64,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct MessageInput {
    #[schemars(length(min = 1))]
    agent_name: String,
    #[schemars(length(min = 1))]
    message: String,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct AgentInput {
    #[schemars(length(min = 1))]
    agent_name: String,
}

#[derive(JsonSchema)]
#[schemars(transparent)]
struct PendingTurnTrue(#[schemars(extend("const" = true))] bool);

#[derive(JsonSchema)]
#[schemars(transparent)]
struct PendingTurnFalse(#[schemars(extend("const" = false))] bool);

#[derive(JsonSchema)]
#[schemars(transparent)]
struct RunningStatus(#[schemars(extend("const" = "running"))] String);

#[derive(JsonSchema)]
#[schemars(transparent)]
struct IdleStatus(#[schemars(extend("const" = "idle"))] String);

#[derive(JsonSchema)]
#[schemars(transparent)]
struct StoppedStatus(#[schemars(extend("const" = "stopped"))] String);

#[derive(JsonSchema)]
#[schemars(transparent)]
struct FailedStatus(#[schemars(extend("const" = "failed"))] String);

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct RunningAgent {
    agent_name: String,
    agent_type: Option<String>,
    status: RunningStatus,
    pending_turn: PendingTurnTrue,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct IdleAgent {
    agent_name: String,
    agent_type: Option<String>,
    status: IdleStatus,
    pending_turn: PendingTurnFalse,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct StoppedAgent {
    agent_name: String,
    agent_type: Option<String>,
    status: StoppedStatus,
    pending_turn: PendingTurnFalse,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct FailedAgent {
    agent_name: String,
    agent_type: Option<String>,
    status: FailedStatus,
    pending_turn: PendingTurnFalse,
    error: String,
}

#[derive(JsonSchema)]
#[schemars(untagged, extend("x-harness-one-of" = true))]
enum ListedAgent {
    Running(RunningAgent),
    Idle(IdleAgent),
    Stopped(StoppedAgent),
    Failed(FailedAgent),
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ListOutput {
    agents: Vec<ListedAgent>,
}

#[derive(JsonSchema)]
#[schemars(tag = "type", rename_all = "lowercase", deny_unknown_fields)]
enum OperationResult {
    Success,
    Error { error: String },
}

#[derive(JsonSchema)]
#[schemars(tag = "type", rename_all = "lowercase", deny_unknown_fields)]
enum StringResult {
    Success { value: String },
    Error { error: String },
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum ToolName {
    List,
    Spawn,
    Wait,
    SendMessage,
    Interrupt,
    Stop,
}

impl ToolName {
    const ALL: [Self; 6] = [
        Self::List,
        Self::Spawn,
        Self::Wait,
        Self::SendMessage,
        Self::Interrupt,
        Self::Stop,
    ];

    pub(crate) fn runtime_tool(self) -> RuntimeBuiltinToolName {
        match self {
            Self::List => RuntimeBuiltinToolName::SubagentList,
            Self::Spawn => RuntimeBuiltinToolName::SubagentSpawn,
            Self::Wait => RuntimeBuiltinToolName::SubagentWait,
            Self::SendMessage => RuntimeBuiltinToolName::SubagentSendMessage,
            Self::Interrupt => RuntimeBuiltinToolName::SubagentInterrupt,
            Self::Stop => RuntimeBuiltinToolName::SubagentStop,
        }
    }

    pub(crate) fn sandbox_name(self) -> &'static str {
        match self {
            Self::List => "agent_list",
            Self::Spawn => "agent_spawn",
            Self::Wait => "agent_wait",
            Self::SendMessage => "agent_message",
            Self::Interrupt => "agent_interrupt",
            Self::Stop => "agent_stop",
        }
    }

    pub(crate) fn function_name(self) -> &'static str {
        match self {
            Self::List => "list",
            Self::Spawn => "spawn",
            Self::Wait => "wait",
            Self::SendMessage => "sendMessage",
            Self::Interrupt => "interrupt",
            Self::Stop => "close",
        }
    }

    fn spec(self) -> ToolSpec {
        match self {
            Self::List => ToolSpec {
                name: self,
                description: "List the subagents owned by this agent session and their current runtime status.",
                input_schema: ListInput::tool_schema(),
                output_schema: ListOutput::tool_schema(),
            },
            Self::Spawn => ToolSpec {
                name: self,
                description: "Spawn a stateful subagent and start its first turn without waiting for completion. agentName must be unique for the lifetime of this parent session; names remain reserved after the subagent is closed and cannot be reused.",
                input_schema: SpawnInput::tool_schema(),
                output_schema: OperationResult::tool_schema(),
            },
            Self::Wait => ToolSpec {
                name: self,
                description: "Wait for the current turn of one subagent and return its final answer.",
                input_schema: WaitInput::tool_schema(),
                output_schema: StringResult::tool_schema(),
            },
            Self::SendMessage => ToolSpec {
                name: self,
                description: "Send a message to a subagent, steering its active turn or starting its next turn.",
                input_schema: MessageInput::tool_schema(),
                output_schema: OperationResult::tool_schema(),
            },
            Self::Interrupt => ToolSpec {
                name: self,
                description: "Interrupt the active turn of one subagent without closing its session.",
                input_schema: AgentInput::tool_schema(),
                output_schema: OperationResult::tool_schema(),
            },
            Self::Stop => ToolSpec {
                name: self,
                description: "Stop and permanently discard one subagent session. Closing releases its resources but does not release its agentName; use a new unique name for future subagents.",
                input_schema: AgentInput::tool_schema(),
                output_schema: OperationResult::tool_schema(),
            },
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn agent_list_schema_preserves_status_specific_contract() {
        // Prepare
        let tools = tools();

        // Do
        let agent_list = tools
            .iter()
            .find(|tool| tool.name == ToolName::List)
            .unwrap();
        let branches = agent_list.output_schema["properties"]["agents"]["items"]["oneOf"]
            .as_array()
            .unwrap();
        let branch = |status: &str| {
            branches
                .iter()
                .find(|branch| branch["properties"]["status"]["const"] == status)
                .unwrap()
        };

        // Assert
        assert_eq!(branches.len(), 4);
        assert_eq!(
            branch("running")["properties"]["pendingTurn"]["const"],
            json!(true)
        );
        for status in ["idle", "stopped", "failed"] {
            assert_eq!(
                branch(status)["properties"]["pendingTurn"]["const"],
                json!(false)
            );
        }
        assert_eq!(
            branch("failed")["required"],
            json!(["agentName", "status", "pendingTurn", "error"])
        );
    }

    #[test]
    fn agent_name_lifetime_is_explicit_in_spawn_and_close_descriptions() {
        // Prepare
        let tools = tools();

        // Do
        let spawn = tools
            .iter()
            .find(|tool| tool.name == ToolName::Spawn)
            .unwrap();
        let close = tools
            .iter()
            .find(|tool| tool.name == ToolName::Stop)
            .unwrap();

        // Assert
        assert!(spawn.description.contains("unique for the lifetime"));
        assert!(spawn.description.contains("cannot be reused"));
        assert!(close.description.contains("does not release its agentName"));
        assert!(close.description.contains("new unique name"));
    }
}
