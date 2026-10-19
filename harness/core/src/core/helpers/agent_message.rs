//! Attributed agent input shared by command and notification handling.

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::core::error::CoreError;
use crate::core::wire::content::{ContentBlock, validate_non_empty_content};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum AgentMessageContext {
    Task {
        sender_agent_path: String,
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
    },
    Failed {
        error: AgentResultError,
    },
    Cancelled {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
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
                content,
            },
            AgentMessageKind::Message => Self::Message {
                sender_agent_path,
                content,
            },
        }
    }
}

pub(crate) fn render_agent_message(
    message: AgentMessageContext,
) -> Result<Vec<ContentBlock>, CoreError> {
    let (sender_agent_path, kind, body) = match message {
        AgentMessageContext::Task {
            sender_agent_path,
            content,
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
                AgentResultTermination::Completed { output } => {
                    format!("Status: completed\nOutput: {output}")
                }
                AgentResultTermination::Failed { error } => {
                    format!("Status: failed\nError: {}", json!(error))
                }
                AgentResultTermination::Cancelled {
                    reason: Some(reason),
                } => format!("Status: cancelled\nReason: {reason}"),
                AgentResultTermination::Cancelled { reason: None } => {
                    "Status: cancelled".to_owned()
                }
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
    let mut content = vec![ContentBlock::text(format!(
        "Agent {kind} from {sender_agent_path}:\n"
    ))];
    content.extend(body);
    Ok(content)
}
