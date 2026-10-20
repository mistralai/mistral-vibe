//! Attributed agent input shared by command and notification handling.

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::{render_template, validate_template};
use crate::core::error::CoreError;
use crate::core::step_protocol::TurnStopReason;
use crate::core::wire::content::{ContentBlock, validate_non_empty_content};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct AgentMessageSettings {
    pub header: String,
    pub task: String,
    pub current_task: String,
    pub inherited_task: String,
    /// Result text for each tool call still running when a conversation is copied to a new agent.
    pub inherited_pending_call: String,
}

impl AgentMessageSettings {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        for (template, tokens, name) in [
            (
                &self.header,
                &["kind", "sender_agent_path"][..],
                "settings.prompts.agent_messages.header",
            ),
            (&self.task, &[][..], "settings.prompts.agent_messages.task"),
            (
                &self.current_task,
                &["recipient_agent_path", "sender_agent_path"][..],
                "settings.prompts.agent_messages.current_task",
            ),
            (
                &self.inherited_task,
                &[
                    "source_agent_path",
                    "recipient_agent_path",
                    "sender_agent_path",
                ][..],
                "settings.prompts.agent_messages.inherited_task",
            ),
            (
                &self.inherited_pending_call,
                &[][..],
                "settings.prompts.agent_messages.inherited_pending_call",
            ),
        ] {
            validate_template(template, tokens, name)?;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum AgentTaskContext {
    #[default]
    Unattributed,
    Current {
        recipient_agent_path: String,
    },
    Inherited {
        recipient_agent_path: String,
        source_agent_path: String,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum AgentMessageContext {
    Task {
        sender_agent_path: String,
        #[serde(default)]
        context: AgentTaskContext,
        content: Vec<ContentBlock>,
    },
    Message {
        sender_agent_path: String,
        content: Vec<ContentBlock>,
    },
    Result {
        sender_agent_path: String,
        termination: AgentResultTermination,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "outcome", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum AgentResultTermination {
    Completed {
        #[serde(default)]
        output: Value,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        stop_reason: Option<TurnStopReason>,
    },
    Failed {
        error: AgentResultError,
    },
    Cancelled {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
        #[serde(default, skip_serializing_if = "Value::is_null")]
        output: Value,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(crate) struct AgentResultError {
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
    #[serde(default)]
    pub details: Value,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum AgentMessageKind {
    Task,
    Message,
}

impl AgentMessageContext {
    pub(crate) fn text(
        sender_agent_path: String,
        kind: AgentMessageKind,
        content: Vec<ContentBlock>,
    ) -> Self {
        match kind {
            AgentMessageKind::Task => Self::Task {
                sender_agent_path,
                context: AgentTaskContext::Unattributed,
                content,
            },
            AgentMessageKind::Message => Self::Message {
                sender_agent_path,
                content,
            },
        }
    }
}

/// `continue_hint` says whether a message from this agent resumes an idle child; a result that
/// did not finish then tells the model so.
pub(crate) fn render_agent_message(
    message: AgentMessageContext,
    settings: &AgentMessageSettings,
    continue_hint: bool,
) -> Result<Vec<ContentBlock>, CoreError> {
    let task_context = match &message {
        AgentMessageContext::Task { context, .. } => Some(context.clone()),
        _ => None,
    };
    let (sender_agent_path, kind, body) = match message {
        AgentMessageContext::Task {
            sender_agent_path,
            content,
            ..
        } => (sender_agent_path, "task", content),
        AgentMessageContext::Message {
            sender_agent_path,
            content,
        } => (sender_agent_path, "message", content),
        AgentMessageContext::Result {
            sender_agent_path,
            termination,
        } => {
            let text = match termination {
                AgentResultTermination::Completed {
                    output,
                    stop_reason: Some(TurnStopReason::IterationLimit),
                } => render_iteration_limit_result(&sender_agent_path, &output, continue_hint),
                AgentResultTermination::Completed { output, .. } => {
                    format!("Status: completed\nOutput: {output}")
                }
                AgentResultTermination::Failed { error } => {
                    format!("Status: failed\nError: {}", json!(error))
                }
                AgentResultTermination::Cancelled { reason, output } => render_cancelled_result(
                    &sender_agent_path,
                    reason.as_deref(),
                    &output,
                    continue_hint,
                ),
            };
            (sender_agent_path, "result", vec![ContentBlock::text(text)])
        }
    };
    if sender_agent_path.trim().is_empty() {
        return Err(CoreError::invalid_command(
            "agent message sender path must not be empty",
        ));
    }
    validate_non_empty_content(&body, "agent message content")?;
    let mut content = vec![ContentBlock::text(
        render_template(
            &settings.header,
            &[("kind", kind), ("sender_agent_path", &sender_agent_path)],
        )
        .map_err(CoreError::invariant)?,
    )];
    if let Some(context) = task_context {
        let text = match context {
            AgentTaskContext::Unattributed => {
                render_template(&settings.task, &[]).map_err(CoreError::invariant)?
            }
            AgentTaskContext::Current {
                recipient_agent_path,
            } => {
                validate_path(&recipient_agent_path)?;
                render_template(
                    &settings.current_task,
                    &[
                        ("recipient_agent_path", &recipient_agent_path),
                        ("sender_agent_path", &sender_agent_path),
                    ],
                )
                .map_err(CoreError::invariant)?
            }
            AgentTaskContext::Inherited {
                recipient_agent_path,
                source_agent_path,
            } => {
                validate_path(&recipient_agent_path)?;
                validate_path(&source_agent_path)?;
                render_template(
                    &settings.inherited_task,
                    &[
                        ("recipient_agent_path", &recipient_agent_path),
                        ("source_agent_path", &source_agent_path),
                        ("sender_agent_path", &sender_agent_path),
                    ],
                )
                .map_err(CoreError::invariant)?
            }
        };
        content.push(ContentBlock::text(text));
    }
    content.extend(body);
    Ok(content)
}

/// The output of a Turn stopped by its iteration limit is the agent's last message, if it had text.
fn render_iteration_limit_result(
    sender_agent_path: &str,
    output: &Value,
    continue_hint: bool,
) -> String {
    let mut text = "Status: stopped, iteration limit reached before the agent finished".to_owned();
    push_partial_output_and_continue_hint(&mut text, sender_agent_path, output, continue_hint);
    text
}

/// A cancelled agent did not finish; it keeps its history and can continue.
fn render_cancelled_result(
    sender_agent_path: &str,
    reason: Option<&str>,
    output: &Value,
    continue_hint: bool,
) -> String {
    let mut text = "Status: cancelled".to_owned();
    if let Some(reason) = reason {
        text.push_str(&format!("\nReason: {reason}"));
    }
    push_partial_output_and_continue_hint(&mut text, sender_agent_path, output, continue_hint);
    text
}

/// Partial output is only the content of the agent's last assistant message. The hint is left out
/// when a message would not start a turn for the idle agent.
fn push_partial_output_and_continue_hint(
    text: &mut String,
    sender_agent_path: &str,
    output: &Value,
    continue_hint: bool,
) {
    let has_output = match output {
        Value::Null => false,
        Value::Array(blocks) => !blocks.is_empty(),
        _ => true,
    };
    if has_output {
        text.push_str(&format!("\nPartial output: {output}"));
    }
    if !continue_hint {
        return;
    }
    text.push_str(&format!(
        "\nTo let it continue with its history, send a message to {sender_agent_path}."
    ));
}

fn validate_path(path: &str) -> Result<(), CoreError> {
    if path.trim().is_empty() {
        return Err(CoreError::invalid_command(
            "agent context path must not be empty",
        ));
    }
    Ok(())
}
