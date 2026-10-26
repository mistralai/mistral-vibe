#![expect(dead_code, reason = "private contract types are reflected by Schemars")]

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::SpawnContext;
use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::tools::schema::ToolSchema;

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ListInput {}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct SpawnInput {
    /// The child's name: one path segment, without `/`, other than `.` or `..`. The child's agent path is this agent's path followed by `/name`.
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
    /// What the child starts from: `"fresh"` (the default), a new conversation with only its task; or `{"mode": "forked", "turns": "all"}`, a copy of this conversation before its task. `turns` is `"all"` (the default) or a positive integer, the number of this agent's latest whole Turns to copy, the current one included.
    #[schemars(default = "default_context")]
    context: SpawnContext,
}

fn default_context() -> SpawnContext {
    SpawnContext::default()
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct MessageInput {
    /// The recipient's agent path: absolute, such as `/root/researcher`, or relative to this agent, such as `researcher` for a direct child.
    #[schemars(length(min = 1))]
    agent_path: String,
    #[schemars(length(min = 1))]
    message: String,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct InterruptInput {
    /// A direct child's agent path: its name, such as `researcher`, or its absolute path, such as `/root/researcher`.
    #[schemars(length(min = 1))]
    agent_path: String,
    /// Why the Turn is interrupted. The child's Turn records it.
    #[schemars(length(min = 1))]
    reason: Option<String>,
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
                InterruptInput::tool_schema(),
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

/// The spawn schema's `name` pattern and `context` alternatives, in words
/// rather than as a regular expression or a list of failed alternatives.
pub(crate) fn explain_spawn_schema_error(
    error: &jsonschema::ValidationError<'_>,
) -> Option<String> {
    let value = error.instance();
    match (error.instance_path().as_str(), error.kind().keyword()) {
        ("/name", "pattern") => Some(format!(
            "at /name: {value} is not a valid name: use one path segment, without `/`, other than `.` or `..`"
        )),
        ("/context", "oneOf" | "anyOf") => Some(invalid_context(value)),
        _ => None,
    }
}

/// What the spawn schema cannot state: `context` must read as a
/// [`SpawnContext`]. Draft 7 counts `3.0` as an integer; serde does not.
pub(crate) fn check_spawn_domain(arguments: &Value) -> Result<(), String> {
    match arguments.get("context") {
        Some(context) if SpawnContext::deserialize(context).is_err() => {
            Err(invalid_context(context))
        }
        _ => Ok(()),
    }
}

fn invalid_context(context: &Value) -> String {
    format!(
        "at /context: {context} is not a valid context: use {}",
        SpawnContext::ACCEPTED
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spawn_schema(conversation_forking: bool) -> Value {
        tools(
            conversation_forking,
            &crate::core::prompt::PromptSettings::default().agent_tools,
        )
        .into_iter()
        .find(|spec| matches!(spec.name, ToolName::Spawn))
        .unwrap()
        .input_schema
    }

    #[test]
    fn omitted_context_advertises_a_fresh_child_conversation() {
        let schema = spawn_schema(true);

        assert_eq!(schema["properties"]["context"]["default"], "fresh");
        assert!(schema["properties"].get("fork_turns").is_none());
    }

    fn check(schema: &Value, arguments: &Value) -> Result<(), String> {
        crate::core::tools::arguments::validate_arguments(
            Some(crate::core::tools::external::RuntimeBuiltinToolName::SubagentSpawn),
            schema,
            arguments,
        )
        .map_err(|rejection| rejection.detail(schema))
    }

    #[test]
    fn a_bad_spawn_context_reads_in_words() {
        let schema = spawn_schema(true);
        for (context, valid) in [
            (serde_json::json!("fresh"), true),
            (serde_json::json!({"mode": "forked"}), true),
            (serde_json::json!({"mode": "forked", "turns": "all"}), true),
            (serde_json::json!({"mode": "forked", "turns": 3}), true),
            (serde_json::json!("all"), false),
            (serde_json::json!({"mode": "forked", "turns": 0}), false),
            (serde_json::json!({"mode": "forked", "turns": "3"}), false),
            (serde_json::json!({"mode": "forked", "turns": 1.5}), false),
            (
                serde_json::json!({"mode": "forked", "turns": 5_000_000_000_u64}),
                false,
            ),
            // The schema accepts this; the domain check does not.
            (serde_json::json!({"mode": "forked", "turns": 3.0}), false),
            (serde_json::json!({"mode": "fresh"}), false),
            (serde_json::json!({"mode": "forked", "extra": 1}), false),
            (serde_json::json!(null), false),
        ] {
            let arguments = serde_json::json!({"name": "a", "message": "hi", "context": context});
            let expected = if valid {
                Ok(())
            } else {
                Err(format!(
                    "at /context: {context} is not a valid context: use \"fresh\" or \
                     {{\"mode\": \"forked\", \"turns\": <positive integer> or \"all\"}}"
                ))
            };
            assert_eq!(check(&schema, &arguments), expected, "{context}");
        }
    }

    #[test]
    fn a_bad_spawn_name_reads_in_words() {
        let schema = spawn_schema(false);
        for (name, valid) in [
            ("a/b", false),
            ("/a", false),
            (".", false),
            ("..", false),
            ("researcher", true),
            ("...", true),
            (".a", true),
            ("a.b", true),
        ] {
            let arguments = serde_json::json!({"name": name, "message": "hi"});
            let expected = if valid {
                Ok(())
            } else {
                Err(format!(
                    "at /name: {} is not a valid name: use one path segment, without `/`, \
                     other than `.` or `..`",
                    serde_json::json!(name)
                ))
            };
            assert_eq!(check(&schema, &arguments), expected, "{name}");
        }
    }

    #[test]
    fn a_spawn_context_is_an_unknown_field_when_forking_is_off() {
        let arguments = serde_json::json!({"name": "a", "message": "hi", "context": "bogus"});

        let error = check(&spawn_schema(false), &arguments).unwrap_err();

        assert!(
            error.starts_with("Additional properties are not allowed ('context' was unexpected)"),
            "{error}"
        );
    }
}
