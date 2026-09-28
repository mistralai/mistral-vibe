use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{CandidateMessage, ReasoningContent};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct TokenUsage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub total_tokens: u64,
    #[serde(default)]
    pub cached_input_tokens: u64,
}

pub(crate) fn validate_token_usage(usage: Option<&TokenUsage>) -> Result<(), &'static str> {
    let Some(usage) = usage else {
        return Ok(());
    };
    let expected_total = usage
        .input_tokens
        .checked_add(usage.output_tokens)
        .ok_or("completion token usage overflowed")?;
    if usage.total_tokens != expected_total {
        return Err("completion total_tokens must equal input_tokens + output_tokens");
    }
    if usage.cached_input_tokens > usage.input_tokens {
        return Err("completion cached_input_tokens must not exceed input_tokens");
    }
    Ok(())
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CompletionFinishReason {
    Stop,
    ToolCall,
    Length,
    ContentFilter,
    Other,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum CompletionResultSemanticPart {
    Reasoning {
        content: Vec<ReasoningContent>,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<serde_json::Map<String, Value>>,
    },
    ToolCall {
        id: String,
        name: String,
        arguments_json: String,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<serde_json::Map<String, Value>>,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(untagged)]
pub(crate) enum CompletionResultPart {
    Content(ContentBlock),
    Semantic(CompletionResultSemanticPart),
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct CompletionResult {
    pub parts: Vec<CompletionResultPart>,
    pub finish_reason: CompletionFinishReason,
    #[serde(default)]
    pub usage: Option<TokenUsage>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct CompletionCandidate {
    pub message: CandidateMessage,
    pub finish_reason: CompletionFinishReason,
    pub usage: Option<TokenUsage>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum AgentCompletionAcceptance {
    Candidate,
    ReplaceAssistantContent { content: Vec<ContentBlock> },
}
