use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

mod result_consistency;
mod tool_identity;

pub(crate) use result_consistency::post_tool_call_warning;
pub(crate) use tool_identity::{HookToolKey, HookToolTarget};

use crate::core::error::CoreError;
use crate::core::tools::external::HookToolCall;
use crate::core::tools::result::model_visible_content_block;
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
    Continue,
    Skip {
        #[serde(default)]
        reason: Vec<ContentBlock>,
    },
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

/// The reason is the result's content and, when it has model-visible text,
/// also the error message: a program calling the tool sees only the message
/// of the error it catches, and the reason must reach the model on both paths.
pub(crate) fn skipped_tool_result(reason: Vec<ContentBlock>) -> Result<ToolResult, CoreError> {
    validate_non_empty_content(&reason, "pre-tool hook skip reason")?;
    let message = skip_reason_text(&reason)
        .unwrap_or_else(|| "Tool execution was skipped by Runtime policy.".to_string());
    Ok(ToolResult::Failure {
        content: reason,
        structured_content: StructuredContent::Absent,
        meta: None,
        error: ProtocolError {
            code: "tool_skipped".to_string(),
            message,
            retryable: false,
            details: Value::Null,
        },
    })
}

fn skip_reason_text(reason: &[ContentBlock]) -> Option<String> {
    let text = reason
        .iter()
        .filter(|block| matches!(block, ContentBlock::Text(_)))
        .filter_map(|block| match model_visible_content_block(block.clone()) {
            Some(ContentBlock::Text(content)) => Some(content.text),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("\n");
    (!text.trim().is_empty()).then_some(text)
}

pub(crate) fn hook_failure_tool_result(error: ProtocolError) -> ToolResult {
    ToolResult::Failure {
        content: text_content(error.message.clone()),
        structured_content: StructuredContent::Absent,
        meta: None,
        error,
    }
}
