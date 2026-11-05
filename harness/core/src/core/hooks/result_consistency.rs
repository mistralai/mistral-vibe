use serde_json::Value;

use crate::core::step_protocol::{Observation, WarningCode};
use crate::core::tools::external::ExternalToolCall;
use crate::core::tools::result::is_model_visible;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::ToolResult;

/// A `warning` when the post-tool-call hooks turned a tool result whose two
/// views agree into one whose views disagree.
///
/// A hook owns the whole result it returns, and Core uses it as returned. The
/// model reads `content` and a program reads `structured_content`, so a hook
/// that rewrites only one of them, such as a redaction of `content`, leaves the
/// other one showing the old value. Core cannot tell such a mistake from an
/// intended change, so it reports it and changes nothing. A result whose views
/// already disagreed before the hooks, such as one with an empty `content`,
/// gets no warning.
pub(crate) fn post_tool_call_warning(
    turn_id: &str,
    call: &ExternalToolCall,
    hook_binding_ids: &[String],
    original: &ToolResult,
    hooked: &ToolResult,
) -> Option<Observation> {
    if hooked == original || !views_agree(original) || views_agree(hooked) {
        return None;
    }
    let tool = call.hook_tool_key().qualified_name;
    Some(Observation::Warning {
        turn_id: Some(turn_id.to_string()),
        code: WarningCode::PostToolCallResultInconsistent,
        message: format!(
            "A post_tool_call hook returned a result for `{tool}` whose content no longer holds \
             its structured_content. The model and programs can read different values; the \
             result is used as returned."
        ),
        details: serde_json::json!({
            "call_id": call.call_id,
            "tool": tool,
            "hook_binding_ids": hook_binding_ids,
        }),
    })
}

/// Whether `content` carries `structured_content`, as MCP asks of a tool that
/// returns structured content: a text block the model sees holds the same
/// JSON value. A result without structured content always agrees. A
/// structured value of the form `{"result": value}`, which wraps a tool's plain
/// return value, also agrees with a text block that holds the wrapped value.
fn views_agree(result: &ToolResult) -> bool {
    let Some(structured) = result.structured_content().filter(|value| !value.is_null()) else {
        return true;
    };
    let wrapped = structured
        .as_object()
        .filter(|object| object.len() == 1)
        .and_then(|object| object.get("result"));
    result
        .content()
        .iter()
        .filter(|block| is_model_visible(block))
        .filter_map(|block| match block {
            ContentBlock::Text(text) => Some(text.text.as_str()),
            _ => None,
        })
        .any(|text| {
            text_holds(text, structured) || wrapped.is_some_and(|value| text_holds(text, value))
        })
}

fn text_holds(text: &str, value: &Value) -> bool {
    value.as_str() == Some(text)
        || serde_json::from_str::<Value>(text).is_ok_and(|parsed| parsed == *value)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn result(value: Value) -> ToolResult {
        serde_json::from_value(value).expect("tool result parses")
    }

    #[test]
    fn views_agree_when_a_visible_text_block_holds_the_structured_value() {
        let cases = [
            (
                json!({"type": "success", "content": [{"type": "text", "text": "{\"a\": 1}"}]}),
                true,
            ),
            (
                json!({"type": "success", "content": [], "structured_content": null}),
                true,
            ),
            (
                json!({
                    "type": "success",
                    "content": [
                        {"type": "text", "text": "{\"a\": 1}"},
                        {"type": "text", "text": "a note"},
                    ],
                    "structured_content": {"a": 1},
                }),
                true,
            ),
            (
                json!({
                    "type": "failure",
                    "content": [{"type": "text", "text": "[1, 2]"}],
                    "structured_content": {"result": [1, 2]},
                    "error": {"code": "e", "message": "m", "retryable": false},
                }),
                true,
            ),
            (
                json!({
                    "type": "success",
                    "content": [{"type": "text", "text": "plain"}],
                    "structured_content": {"result": "plain"},
                }),
                true,
            ),
            (
                json!({
                    "type": "success",
                    "content": [{"type": "text", "text": "REDACTED"}],
                    "structured_content": {"a": 1},
                }),
                false,
            ),
            (
                json!({"type": "success", "content": [], "structured_content": {"a": 1}}),
                false,
            ),
            (
                json!({
                    "type": "success",
                    "content": [{
                        "type": "text",
                        "text": "{\"a\": 1}",
                        "annotations": {"audience": ["user"]},
                    }],
                    "structured_content": {"a": 1},
                }),
                false,
            ),
        ];
        for (value, expected) in cases {
            assert_eq!(views_agree(&result(value.clone())), expected, "{value}");
        }
    }
}
