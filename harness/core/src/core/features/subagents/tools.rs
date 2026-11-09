#![expect(dead_code, reason = "private contract types are reflected by Schemars")]

use jsonschema::error::ValidationErrorKind;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::helpers::schema_error_detail;

use crate::core::SpawnContext;
use crate::core::conversation::{ForkedContext, FreshMode};
use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::tools::schema::ToolSchema;

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ListInput {}

/// A generic child, which needs `instructions`, or a child from `config_name`, which has its own.
#[derive(JsonSchema)]
#[schemars(untagged, extend("x-harness-one-of" = true))]
enum SpawnInput {
    Generic(GenericSpawn),
    Named(NamedSpawn),
}

/// A fresh generic child, which needs `instructions`; a forked generic child, which keeps yours; or a child from `config_name`, which has its own.
#[derive(JsonSchema)]
#[schemars(untagged, extend("x-harness-one-of" = true))]
enum ConversationSpawnInput {
    Generic(FreshGenericSpawn),
    Forked(ForkedGenericSpawn),
    Named(NamedConversationSpawn),
}

/// What every child gets: its name and its first message.
#[derive(JsonSchema)]
struct ChildTask {
    /// The child's name: one path segment, without `/`, other than `.` or `..`. The child's agent path is your path followed by `/name`.
    #[schemars(length(min = 1), regex(pattern = r"^(?!\.{1,2}$)[^/]+$"))]
    name: String,
    /// The child's task, sent as its first message. Every child needs one.
    #[schemars(length(min = 1))]
    message: String,
}

/// A generic child: it has your configuration but not your instructions.
#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct GenericSpawn {
    #[schemars(flatten)]
    task: ChildTask,
    /// The child's instructions, written for its task: it does not get yours.
    #[schemars(length(min = 1))]
    instructions: String,
}

/// A child from a named configuration: it has that configuration's instructions.
#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct NamedSpawn {
    #[schemars(flatten)]
    task: ChildTask,
    #[schemars(length(min = 1))]
    config_name: String,
}

/// A generic child with a fresh conversation: it has your configuration but not your instructions.
#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct FreshGenericSpawn {
    #[schemars(flatten)]
    spawn: GenericSpawn,
    /// `"fresh"` (the default): a new conversation with only its task.
    #[schemars(default = "default_fresh")]
    context: FreshMode,
}

/// A generic child forked from this conversation: it has your configuration and your instructions.
#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ForkedGenericSpawn {
    #[schemars(flatten)]
    task: ChildTask,
    /// More instructions for the child's task. They follow the copied conversation, with its task.
    #[schemars(length(min = 1))]
    instructions: Option<String>,
    /// A copy of this conversation before its task. `turns` is `"all"` (the default) or a positive integer, the number of this agent's latest whole Turns to copy, the current one included.
    context: ForkedContext,
}

/// A child from a named configuration: it has that configuration's instructions.
#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct NamedConversationSpawn {
    #[schemars(flatten)]
    spawn: NamedSpawn,
    /// What the child starts from: `"fresh"` (the default), a new conversation with only its task; or `{"mode": "forked", "turns": "all"}`, a copy of this conversation before its task. `turns` is `"all"` (the default) or a positive integer, the number of this agent's latest whole Turns to copy, the current one included.
    #[schemars(default = "default_context")]
    context: SpawnContext,
}

fn default_fresh() -> FreshMode {
    FreshMode::Fresh
}

fn default_context() -> SpawnContext {
    SpawnContext::default()
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct MessageInput {
    /// The recipient's agent path: absolute, such as `/root/researcher`, or relative to you, such as `researcher` for a direct child.
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

#[derive(JsonSchema)]
#[schemars(tag = "type", rename_all = "lowercase", deny_unknown_fields)]
enum MessageResult {
    /// The recipient received the message: it starts or joins the recipient's Turn.
    Success,
    /// The recipient is idle and this message cannot start its Turn. The message waits in its history until something else starts its next Turn, so no reply comes from it.
    Queued {
        /// Why the message started no Turn.
        reason: String,
    },
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
                MessageResult::tool_schema(),
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

/// A spawn the schema rejected, in words: the schema's alternatives would
/// otherwise read as "not valid under any of the given schemas". The reported
/// alternative is the one the arguments' shape asks for: named with
/// `config_name`, forked with an object `context`, otherwise generic.
pub(crate) fn explain_spawn_schema_error(
    schema: &Value,
    error: &jsonschema::ValidationError<'_>,
) -> Option<String> {
    let ValidationErrorKind::OneOfNotValid { context: variants } = error.kind() else {
        return explain_field_error(error);
    };
    if !error.instance_path().as_str().is_empty() {
        return explain_field_error(error);
    }
    let arguments = error.instance();
    let forking = variants.len() == 3;
    let named = arguments
        .get("config_name")
        .is_some_and(|name| !name.is_null());
    let forked = forking && arguments.get("context").is_some_and(Value::is_object);
    let variant = match (named, forked) {
        (true, _) => variants.len() - 1,
        (false, true) => 1,
        (false, false) => 0,
    };
    let errors = variants.get(variant)?;
    let explained = errors
        .iter()
        .find_map(|error| explain_variant_error(schema, arguments, error, named));
    explained.or_else(|| {
        errors
            .iter()
            .any(|error| is_missing_instructions(error))
            .then(|| missing_instructions(forking))
    })
}

/// One error of the chosen alternative, other than missing `instructions`,
/// which the caller reports only when nothing else is wrong.
fn explain_variant_error(
    schema: &Value,
    arguments: &Value,
    error: &jsonschema::ValidationError<'_>,
    named: bool,
) -> Option<String> {
    if let Some(explained) = explain_field_error(error) {
        return Some(explained);
    }
    let path = error.instance_path().as_str();
    if path == "/context" || path.starts_with("/context/") {
        return Some(invalid_context(arguments.get("context")?));
    }
    if let ValidationErrorKind::AdditionalProperties { unexpected } = error.kind()
        && named
        && unexpected.iter().any(|field| field == "instructions")
    {
        return Some(NAMED_CHILD_INSTRUCTIONS.to_string());
    }
    if is_missing_instructions(error) {
        return None;
    }
    Some(schema_error_detail(schema, error))
}

/// The `name` pattern and the `context` alternatives, in words rather than as
/// a regular expression or a list of failed alternatives.
fn explain_field_error(error: &jsonschema::ValidationError<'_>) -> Option<String> {
    let value = error.instance();
    match (error.instance_path().as_str(), error.kind().keyword()) {
        ("/name", "pattern") => Some(format!(
            "at /name: {value} is not a valid name: use one path segment, without `/`, other than `.` or `..`"
        )),
        ("/context", "oneOf" | "anyOf") => Some(invalid_context(value)),
        _ => None,
    }
}

fn is_missing_instructions(error: &jsonschema::ValidationError<'_>) -> bool {
    matches!(error.kind(), ValidationErrorKind::Required { property } if property == "instructions")
}

/// What the spawn schema cannot state: `context` must read as a
/// [`SpawnContext`] (Draft 7 counts `3.0` as an integer; serde does not).
pub(crate) fn check_spawn_domain(arguments: &Value) -> Result<(), String> {
    match arguments.get("context") {
        Some(context) if SpawnContext::deserialize(context).is_err() => {
            Err(invalid_context(context))
        }
        _ => Ok(()),
    }
}

fn missing_instructions(forking: bool) -> String {
    let unless = if forking { " unless it is forked" } else { "" };
    format!(
        "at /instructions: a child without `config_name` needs `instructions`{unless}: it does \
         not get yours. Write instructions for the child's task"
    )
}

const NAMED_CHILD_INSTRUCTIONS: &str = "at /instructions: a child from `config_name` has that \
     configuration's instructions: omit `instructions`";

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
        let [generic, forked, named] = schema["oneOf"].as_array().unwrap().as_slice() else {
            panic!("{schema}");
        };

        assert_eq!(generic["properties"]["context"]["default"], "fresh");
        assert_eq!(named["properties"]["context"]["default"], "fresh");
        assert!(
            forked["required"]
                .as_array()
                .unwrap()
                .contains(&serde_json::json!("context"))
        );
        assert!(
            [generic, forked, named]
                .iter()
                .all(|variant| variant["properties"].get("fork_turns").is_none())
        );
    }

    #[test]
    fn the_spawn_schema_requires_instructions_exactly_for_a_fresh_generic_child() {
        for (forking, expected) in [
            (false, vec![Some(true), None]),
            (true, vec![Some(true), Some(false), None]),
        ] {
            let schema = spawn_schema(forking);

            let requires: Vec<Option<bool>> = schema["oneOf"]
                .as_array()
                .unwrap()
                .iter()
                .map(|variant| {
                    variant["properties"].get("instructions").map(|_| {
                        variant["required"]
                            .as_array()
                            .unwrap()
                            .contains(&serde_json::json!("instructions"))
                    })
                })
                .collect();

            assert_eq!(requires, expected, "forking: {forking}");
        }
    }

    #[test]
    fn every_spawn_variant_describes_message() {
        for forking in [false, true] {
            let schema = spawn_schema(forking);

            let variants = schema["oneOf"].as_array().unwrap();

            for variant in variants {
                let message = &variant["properties"]["message"];
                assert!(message.get("description").is_some(), "forking: {forking}");
            }
        }
    }

    ///
    /// *Prepare*: A Session with subagents and conversation forking on.
    /// *Do*: Load the details of `agents.spawn` through tool discovery, as the model does.
    /// *Assert*: Its argument is a union of three alternatives: a fresh generic child, whose
    /// `instructions` are required; a forked one, whose `instructions` are optional; and a
    /// named one, without `instructions`.
    ///
    #[test]
    fn the_spawn_declaration_the_model_reads_requires_instructions_for_a_fresh_generic_child() {
        // Prepare
        let mut config = crate::core::testing::config();
        config.settings.tools.subagents =
            crate::core::features::subagents::Mode::enabled().with_conversation_forking(true);

        // Do
        let details = crate::core::tools::resolved::testing::search(
            &config,
            crate::core::features::tool_discovery::SearchRequest {
                mode: crate::core::features::tool_discovery::SearchMode::Details,
                functions: vec!["agents.spawn".to_string()],
                ..Default::default()
            },
        );

        // Assert
        let alternatives: Vec<&str> = details.split("  } | {\n").collect();
        assert_eq!(alternatives.len(), 3, "{details}");
        let with = |marker: &str| -> &str {
            let found: Vec<&&str> = alternatives
                .iter()
                .filter(|alternative| alternative.contains(marker))
                .collect();
            assert_eq!(found.len(), 1, "{marker} in {details}");
            found[0]
        };
        let fresh = with("    context?: \"fresh\";\n");
        let forked = with("    context: {\n      mode: \"forked\";");
        let named = with("    config_name: string;\n");
        assert!(fresh.contains("    instructions: string;\n"), "{details}");
        assert!(
            forked.contains("    instructions?: null | string;\n"),
            "{details}"
        );
        assert!(!named.contains("    instructions"), "{details}");
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
            let arguments = serde_json::json!({
                "name": "a", "message": "hi", "instructions": "Work.", "context": context
            });
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
            let arguments =
                serde_json::json!({"name": name, "message": "hi", "instructions": "Work."});
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
    fn a_forked_generic_child_keeps_its_parents_instructions_unless_given_more() {
        let schema = spawn_schema(true);
        for (arguments, expected) in [
            (
                serde_json::json!({"name": "a", "message": "hi", "context": {"mode": "forked"}}),
                Ok(()),
            ),
            (
                serde_json::json!({
                    "name": "a", "message": "hi", "instructions": "Work.",
                    "context": {"mode": "forked", "turns": 2}
                }),
                Ok(()),
            ),
            (
                serde_json::json!({"name": "a", "message": "hi", "context": "fresh"}),
                Err(missing_instructions(true)),
            ),
            (
                serde_json::json!({
                    "name": "a", "message": "hi", "config_name": "researcher",
                    "instructions": "Work.", "context": {"mode": "forked"}
                }),
                Err(NAMED_CHILD_INSTRUCTIONS.to_string()),
            ),
        ] {
            assert_eq!(check(&schema, &arguments), expected, "{arguments}");
        }
    }

    #[test]
    fn a_generic_child_needs_instructions_and_a_named_child_takes_none() {
        for forking in [false, true] {
            let schema = spawn_schema(forking);
            for (arguments, expected) in [
                (
                    serde_json::json!({"name": "a", "message": "hi"}),
                    Err(missing_instructions(forking)),
                ),
                (
                    serde_json::json!({"name": "a", "message": "hi", "instructions": "Work."}),
                    Ok(()),
                ),
                (
                    serde_json::json!({"name": "a", "message": "hi", "config_name": "researcher"}),
                    Ok(()),
                ),
                (
                    serde_json::json!({
                        "name": "a", "message": "hi", "config_name": "researcher",
                        "instructions": "Work."
                    }),
                    Err(NAMED_CHILD_INSTRUCTIONS.to_string()),
                ),
            ] {
                assert_eq!(check(&schema, &arguments), expected, "{arguments}");
            }
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
