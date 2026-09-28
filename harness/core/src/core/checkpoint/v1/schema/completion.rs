use std::collections::HashSet;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::wire::completion::{
    CompletionCandidate, CompletionFinishReason, TokenUsage, validate_token_usage,
};
use crate::core::wire::message::{AssistantPart, AssistantSemanticPart, ToolArguments};

use super::message::CheckpointCandidateMessage;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum CheckpointCompletionFinishReason {
    Stop,
    ToolCall,
    Length,
    ContentFilter,
    Other,
}

impl CheckpointCompletionFinishReason {
    fn capture(reason: &CompletionFinishReason) -> Self {
        match reason {
            CompletionFinishReason::Stop => Self::Stop,
            CompletionFinishReason::ToolCall => Self::ToolCall,
            CompletionFinishReason::Length => Self::Length,
            CompletionFinishReason::ContentFilter => Self::ContentFilter,
            CompletionFinishReason::Other => Self::Other,
        }
    }

    fn restore(self) -> CompletionFinishReason {
        match self {
            Self::Stop => CompletionFinishReason::Stop,
            Self::ToolCall => CompletionFinishReason::ToolCall,
            Self::Length => CompletionFinishReason::Length,
            Self::ContentFilter => CompletionFinishReason::ContentFilter,
            Self::Other => CompletionFinishReason::Other,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct CheckpointTokenUsage {
    input_tokens: u64,
    output_tokens: u64,
    total_tokens: u64,
    #[serde(default)]
    cached_input_tokens: u64,
}

impl CheckpointTokenUsage {
    fn capture(usage: &TokenUsage) -> Self {
        Self {
            input_tokens: usage.input_tokens,
            output_tokens: usage.output_tokens,
            total_tokens: usage.total_tokens,
            cached_input_tokens: usage.cached_input_tokens,
        }
    }

    fn restore(self) -> TokenUsage {
        TokenUsage {
            input_tokens: self.input_tokens,
            output_tokens: self.output_tokens,
            total_tokens: self.total_tokens,
            cached_input_tokens: self.cached_input_tokens,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) struct CheckpointCompletionCandidate {
    message: CheckpointCandidateMessage,
    finish_reason: CheckpointCompletionFinishReason,
    usage: Option<CheckpointTokenUsage>,
}

impl CheckpointCompletionCandidate {
    pub(in crate::core::checkpoint::v1) fn capture(
        candidate: &CompletionCandidate,
    ) -> Result<Self, String> {
        validate_restored_candidate(candidate)?;
        Ok(Self {
            message: CheckpointCandidateMessage::capture(&candidate.message),
            finish_reason: CheckpointCompletionFinishReason::capture(&candidate.finish_reason),
            usage: candidate.usage.as_ref().map(CheckpointTokenUsage::capture),
        })
    }

    pub(in crate::core::checkpoint::v1) fn restore(self) -> Result<CompletionCandidate, String> {
        let candidate = CompletionCandidate {
            message: self.message.restore(),
            finish_reason: self.finish_reason.restore(),
            usage: self.usage.map(CheckpointTokenUsage::restore),
        };
        validate_restored_candidate(&candidate)?;
        Ok(candidate)
    }
}

fn validate_restored_candidate(candidate: &CompletionCandidate) -> Result<(), String> {
    validate_token_usage(candidate.usage.as_ref()).map_err(str::to_string)?;
    if candidate.message.content.is_empty() {
        return Err("assistant completion must contain at least one part".to_string());
    }

    let mut tool_call_ids = HashSet::new();
    for part in &candidate.message.content {
        match part {
            AssistantPart::Semantic(AssistantSemanticPart::Reasoning { content, .. })
                if content.is_empty() =>
            {
                return Err("completion reasoning must contain at least one item".to_string());
            }
            AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                id,
                name,
                arguments,
                ..
            }) => {
                if id.trim().is_empty() || name.trim().is_empty() {
                    return Err("tool call IDs and names must not be empty".to_string());
                }
                if !tool_call_ids.insert(id) {
                    return Err(format!("duplicate tool call ID {id:?}"));
                }
                validate_tool_arguments(arguments)?;
            }
            AssistantPart::Content(_)
            | AssistantPart::Semantic(AssistantSemanticPart::Reasoning { .. }) => {}
        }
    }

    let has_tool_call = !tool_call_ids.is_empty();
    match candidate.finish_reason {
        CompletionFinishReason::ToolCall if !has_tool_call => {
            Err("tool_call finish requires at least one tool call".to_string())
        }
        CompletionFinishReason::ToolCall => Ok(()),
        _ if has_tool_call => Err("only tool_call finish may contain a tool call".to_string()),
        _ => Ok(()),
    }
}

fn validate_tool_arguments(arguments: &ToolArguments) -> Result<(), String> {
    match arguments {
        ToolArguments::Json { raw, value } => {
            let parsed = serde_json::from_str::<Value>(raw).map_err(|error| {
                format!("checkpoint JSON tool arguments contain invalid raw JSON: {error}")
            })?;
            if &parsed != value {
                return Err(
                    "checkpoint JSON tool arguments do not match their raw JSON".to_string()
                );
            }
        }
        ToolArguments::InvalidJson { raw, error } => {
            if error.trim().is_empty() {
                return Err("checkpoint invalid JSON tool arguments need an error".to_string());
            }
            if serde_json::from_str::<Value>(raw).is_ok() {
                return Err(
                    "checkpoint invalid JSON tool arguments contain valid raw JSON".to_string(),
                );
            }
        }
    }
    Ok(())
}
