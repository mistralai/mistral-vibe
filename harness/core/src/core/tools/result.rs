use serde_json::Value;
use serde_json::json;

use crate::core::wire::content::{
    ContentBlock, Role, strip_model_hidden_content_fields, text_content,
};
use crate::core::wire::message::{Message, ToolOutcome};
use crate::core::wire::tool::{ProtocolError, StructuredContent, ToolResult};

pub(crate) fn invalid_tool_call_result(reason: String) -> ToolResult {
    ToolResult::Failure {
        content: text_content(
            json!({
                "result": {
                    "type": "error",
                    "error": {"name": "InvalidToolCall", "message": reason}
                }
            })
            .to_string(),
        ),
        structured_content: StructuredContent::Absent,
        meta: None,
        error: ProtocolError {
            code: "invalid_tool_call".to_string(),
            message: reason,
            retryable: false,
            details: Value::Null,
        },
    }
}

pub(crate) fn model_tool_result_content(result: &ToolResult) -> Vec<ContentBlock> {
    let structured_content = result.structured_content().filter(|value| !value.is_null());
    let content = match result {
        ToolResult::Success { content, .. } if !content.is_empty() => content.clone(),
        ToolResult::Success { .. } if structured_content.is_some() => {
            let value = structured_content.expect("guarded structured content");
            text_content(structured_content_text(value))
        }
        ToolResult::Success { .. } => Vec::new(),
        ToolResult::Failure { content, .. } if !content.is_empty() => content.clone(),
        ToolResult::Failure { .. } if structured_content.is_some() => {
            let value = structured_content.expect("guarded structured content");
            text_content(structured_content_text(value))
        }
        ToolResult::Failure { error, .. } => text_content(
            json!({
                "result": {
                    "type": "error",
                    "error": {
                        "name": error.code,
                        "message": error.message,
                        "details": error.details,
                    }
                }
            })
            .to_string(),
        ),
    };
    content
        .into_iter()
        .filter_map(model_visible_content_block)
        .collect()
}

pub(crate) fn model_visible_content_block(mut block: ContentBlock) -> Option<ContentBlock> {
    let annotations = match &block {
        ContentBlock::Text(content) => &content.annotations,
        ContentBlock::Image(content) => &content.annotations,
        ContentBlock::Audio(content) => &content.annotations,
        ContentBlock::ResourceLink(resource) => &resource.annotations,
        ContentBlock::Resource(content) => &content.annotations,
        _ => return None,
    };
    if annotations.as_ref().is_some_and(|annotations| {
        annotations
            .audience
            .as_ref()
            .is_some_and(|audience| !audience.is_empty() && !audience.contains(&Role::Assistant))
    }) {
        return None;
    }
    strip_model_hidden_content_fields(&mut block);
    Some(block)
}

pub(crate) fn model_tool_result_message(
    call_id: String,
    name: String,
    result: &ToolResult,
) -> Message {
    let outcome = match result {
        ToolResult::Success { .. } => ToolOutcome::Success,
        ToolResult::Failure { .. } => ToolOutcome::Failure,
    };
    Message::tool(
        call_id,
        name,
        outcome,
        model_tool_result_content(result),
        None,
    )
}

fn structured_content_text(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_string)
        .unwrap_or_else(|| value.to_string())
}
