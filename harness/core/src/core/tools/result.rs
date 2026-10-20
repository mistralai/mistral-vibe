use serde_json::Value;
use serde_json::json;

use crate::core::features::permissions::ApprovalFailureReason;
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

pub(crate) fn permission_denied_result() -> ToolResult {
    const MESSAGE: &str = "Tool call denied by permission policy.";

    permission_failure_result(MESSAGE)
}

pub(crate) fn approval_rejected_result() -> ToolResult {
    const MESSAGE: &str = "Tool call was rejected during approval.";

    permission_failure_result(MESSAGE)
}

fn permission_failure_result(message: &'static str) -> ToolResult {
    ToolResult::Failure {
        content: text_content(message),
        structured_content: StructuredContent::Absent,
        meta: None,
        error: ProtocolError {
            code: "permission_denied".to_string(),
            message: message.to_string(),
            retryable: false,
            details: Value::Null,
        },
    }
}

pub(crate) fn approval_failed_result(reason: ApprovalFailureReason) -> ToolResult {
    let message = reason.model_message();

    ToolResult::Failure {
        content: text_content(message),
        structured_content: StructuredContent::Absent,
        meta: None,
        error: ProtocolError {
            code: "approval_failed".to_string(),
            message: message.to_string(),
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
        // A failure with output, such as a command that timed out after
        // printing, shows the error beside the output: either alone would hide
        // why the call failed or what it did before failing.
        ToolResult::Failure { error, .. } => {
            let mut result = json!({
                "type": "error",
                "error": {
                    "name": error.code,
                    "message": error.message,
                    "details": error.details,
                }
            });
            if let Some(output) = structured_content {
                result["output"] = output.clone();
            }
            text_content(json!({ "result": result }).to_string())
        }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn timed_out(content: Vec<ContentBlock>, structured_content: StructuredContent) -> ToolResult {
        ToolResult::Failure {
            content,
            structured_content,
            meta: None,
            error: ProtocolError {
                code: "timeout".to_string(),
                message: "Command timed out after 5 seconds".to_string(),
                retryable: false,
                details: Value::Null,
            },
        }
    }

    fn only_text(blocks: Vec<ContentBlock>) -> String {
        let [ContentBlock::Text(content)] = blocks.as_slice() else {
            panic!("expected one text block, got {blocks:?}");
        };
        content.text.clone()
    }

    #[test]
    fn a_failure_with_output_shows_the_error_beside_the_output() {
        let output = json!({"stdout": "step 1 done\n", "stderr": "", "returncode": -1});
        let text = only_text(model_tool_result_content(&timed_out(
            Vec::new(),
            StructuredContent::present(output.clone()),
        )));
        let rendered: Value = serde_json::from_str(&text).expect("failure renders as JSON");
        assert_eq!(
            rendered,
            json!({
                "result": {
                    "type": "error",
                    "error": {
                        "name": "timeout",
                        "message": "Command timed out after 5 seconds",
                        "details": null,
                    },
                    "output": output,
                }
            })
        );
    }

    #[test]
    fn a_failure_without_output_shows_the_error_alone() {
        let text = only_text(model_tool_result_content(&timed_out(
            Vec::new(),
            StructuredContent::Absent,
        )));
        let rendered: Value = serde_json::from_str(&text).expect("failure renders as JSON");
        assert!(rendered["result"].get("output").is_none());
        assert_eq!(
            rendered["result"]["error"]["message"],
            "Command timed out after 5 seconds"
        );
    }

    #[test]
    fn a_failure_with_its_own_content_keeps_it() {
        let text = only_text(model_tool_result_content(&timed_out(
            text_content("partial output and the timeout"),
            StructuredContent::present(json!({"stdout": "ignored"})),
        )));
        assert_eq!(text, "partial output and the timeout");
    }
}
