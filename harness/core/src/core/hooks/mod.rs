use std::collections::BTreeSet;

use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

mod tool_identity;

pub(crate) use tool_identity::{HookToolKey, HookToolTarget};

use crate::core::error::CoreError;
use crate::core::tools::external::HookToolCall;
use crate::core::wire::completion::{AgentCompletionAcceptance, CompletionCandidate};
use crate::core::wire::content::{ContentBlock, text_content, validate_non_empty_content};
use crate::core::wire::tool::{ProtocolError, StructuredContent, ToolResult};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub(crate) enum HookPoint {
    PreAgentTurn,
    PreLlmCall,
    PostLlmCall,
    PostAgentTurn,
    PreToolCall,
    PostToolCall,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum PreAgentTurnOutput {
    Continue { user_content: Vec<ContentBlock> },
    Skip { reason: Vec<ContentBlock> },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum PreLlmCallOutput {
    Continue {
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        context_messages: Vec<KeyedContextMessage>,
    },
    Skip {
        #[serde(default)]
        reason: Vec<ContentBlock>,
    },
}

/// Model-only context returned by a `pre_llm_call` hook.
///
/// `key` identifies one context item across hook calls. Before the model call,
/// Core injects `content` as a user message unless the latest injection for
/// that key has identical content. Changed content is appended rather than
/// replacing the earlier message. This context never appears in public history.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(crate) struct KeyedContextMessage {
    pub key: String,
    pub content: Vec<ContentBlock>,
}

pub(crate) fn validate_keyed_context_messages(
    messages: &[KeyedContextMessage],
) -> Result<(), CoreError> {
    let mut keys = BTreeSet::new();
    for message in messages {
        if message.key.is_empty() {
            return Err(CoreError::invalid_command(
                "pre-LLM hook context key must not be empty",
            ));
        }
        validate_non_empty_content(&message.content, "pre-LLM hook context content")?;
        if !keys.insert(message.key.as_str()) {
            return Err(CoreError::invalid_command(format!(
                "pre-LLM hook context key {:?} appears more than once",
                message.key
            )));
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum CompletionHookOutput {
    Accept {
        acceptance: AgentCompletionAcceptance,
    },
    Retry {
        feedback: Vec<ContentBlock>,
    },
    Reject {
        reason: Vec<ContentBlock>,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum PreToolCallOutput {
    Continue {
        effective_arguments: serde_json::Value,
    },
    Skip {
        reason: Vec<ContentBlock>,
    },
}

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "hook", content = "input", rename_all = "snake_case")]
pub(crate) enum HookCall {
    PreAgentTurn {
        user_content: Vec<ContentBlock>,
    },
    PreLlmCall,
    PostLlmCall {
        candidate: CompletionCandidate,
    },
    PostAgentTurn {
        candidate: CompletionCandidate,
    },
    PreToolCall {
        tool_call: HookToolCall,
    },
    PostToolCall {
        tool_call: HookToolCall,
        tool_result: ToolResult,
    },
}

impl HookCall {
    pub(crate) fn point(&self) -> HookPoint {
        match self {
            Self::PreAgentTurn { .. } => HookPoint::PreAgentTurn,
            Self::PreLlmCall => HookPoint::PreLlmCall,
            Self::PostLlmCall { .. } => HookPoint::PostLlmCall,
            Self::PostAgentTurn { .. } => HookPoint::PostAgentTurn,
            Self::PreToolCall { .. } => HookPoint::PreToolCall,
            Self::PostToolCall { .. } => HookPoint::PostToolCall,
        }
    }
}

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "hook", content = "output", rename_all = "snake_case")]
pub(crate) enum HookResult {
    PreAgentTurn(PreAgentTurnOutput),
    PreLlmCall(PreLlmCallOutput),
    PostLlmCall(CompletionHookOutput),
    PostAgentTurn(CompletionHookOutput),
    PreToolCall(PreToolCallOutput),
    PostToolCall { tool_result: ToolResult },
}

impl HookResult {
    pub(crate) fn point(&self) -> HookPoint {
        match self {
            Self::PreAgentTurn(_) => HookPoint::PreAgentTurn,
            Self::PreLlmCall(_) => HookPoint::PreLlmCall,
            Self::PostLlmCall(_) => HookPoint::PostLlmCall,
            Self::PostAgentTurn(_) => HookPoint::PostAgentTurn,
            Self::PreToolCall(_) => HookPoint::PreToolCall,
            Self::PostToolCall { .. } => HookPoint::PostToolCall,
        }
    }
}

pub(crate) fn hook_action_id(subject_action_id: &str, point: HookPoint) -> String {
    let suffix = match point {
        HookPoint::PreAgentTurn => "pre_agent_turn",
        HookPoint::PreLlmCall => "pre_llm_call",
        HookPoint::PostLlmCall => "post_llm_call",
        HookPoint::PostAgentTurn => "post_agent_turn",
        HookPoint::PreToolCall => "pre_tool_call",
        HookPoint::PostToolCall => "post_tool_call",
    };
    crate::core::action_id::hook(subject_action_id, suffix)
}

pub(crate) fn skipped_tool_result(reason: Vec<ContentBlock>) -> Result<ToolResult, CoreError> {
    validate_non_empty_content(&reason, "pre-tool hook skip reason")?;
    Ok(ToolResult::Failure {
        content: reason,
        structured_content: StructuredContent::Absent,
        meta: None,
        error: ProtocolError {
            code: "tool_skipped".to_string(),
            message: "Tool execution was skipped by Runtime policy.".to_string(),
            retryable: false,
            details: Value::Null,
        },
    })
}

pub(crate) fn hook_failure_tool_result(error: ProtocolError) -> ToolResult {
    ToolResult::Failure {
        content: text_content(error.message.clone()),
        structured_content: StructuredContent::Absent,
        meta: None,
        error,
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn pre_llm_continue_context_is_optional_and_closed() {
        let bare = serde_json::from_value::<PreLlmCallOutput>(json!({"type": "continue"}))
            .expect("bare continue decodes");
        assert_eq!(
            bare,
            PreLlmCallOutput::Continue {
                context_messages: Vec::new(),
            }
        );
        assert_eq!(
            serde_json::to_value(&bare).expect("continue encodes"),
            json!({"type": "continue"})
        );

        let keyed = json!({
            "type": "continue",
            "context_messages": [{
                "key": "github",
                "content": [{"type": "text", "text": "GitHub is connected."}],
            }],
        });
        let decoded = serde_json::from_value::<PreLlmCallOutput>(keyed.clone())
            .expect("keyed continue decodes");
        assert_eq!(
            serde_json::to_value(&decoded).expect("keyed continue encodes"),
            keyed
        );

        let error = serde_json::from_value::<PreLlmCallOutput>(json!({
            "type": "continue",
            "context_messages": [{
                "key": "github",
                "content": [{"type": "text", "text": "GitHub is connected."}],
                "role": "system",
            }],
        }))
        .expect_err("unknown context field is rejected");
        assert!(
            error.to_string().contains("unknown field `role`"),
            "{error}"
        );
    }
}
