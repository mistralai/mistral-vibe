use crate::core::step_protocol::{Observation, WarningCode};
use crate::core::tools::external::ExternalToolCall;
use crate::core::wire::tool::{ProtocolError, ToolResult};

use super::hook_failure_tool_result;

/// The schema error quotes the rejected value whole. The value comes from a
/// hook, so it can be large or hold a secret, and the detail reaches the model,
/// the warning and every log of it. A short prefix still locates the mistake.
const DETAIL_LIMIT_CHARS: usize = 300;

/// The failed result and the `warning` for a call whose pre-tool-call hooks
/// returned arguments that do not satisfy the tool's input schema.
///
/// The hooks own the arguments they return, so the mismatch is a hook mistake,
/// not a model mistake and not a malformed command. The call fails as if its
/// hook had failed: the tool does not run, the model reads the failure, and
/// the turn goes on. The warning names the hook bindings, so the Runtime can
/// report which hooks to fix.
pub(crate) fn invalid_pre_tool_call_arguments(
    turn_id: &str,
    call: &ExternalToolCall,
    hook_binding_ids: &[String],
    detail: &str,
) -> (ToolResult, Observation) {
    let tool = call.hook_tool_key().qualified_name;
    let detail = shortened(detail);
    let message = format!(
        "A pre_tool_call hook returned arguments for `{tool}` that do not satisfy its input \
         schema: {detail}. The tool did not run."
    );
    let result = hook_failure_tool_result(ProtocolError {
        code: "pre_tool_call_arguments_invalid".to_string(),
        message: message.clone(),
        retryable: false,
        details: serde_json::Value::Null,
    });
    let warning = Observation::Warning {
        turn_id: Some(turn_id.to_string()),
        code: WarningCode::PreToolCallArgumentsInvalid,
        message,
        details: serde_json::json!({
            "call_id": call.call_id,
            "tool": tool,
            "hook_binding_ids": hook_binding_ids,
        }),
    };
    (result, warning)
}

fn shortened(detail: &str) -> String {
    match detail.char_indices().nth(DETAIL_LIMIT_CHARS) {
        Some((end, _)) => format!("{}…", &detail[..end]),
        None => detail.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_long_detail_keeps_only_its_start() {
        let detail = format!(
            "at /n: \"{}\" is not of type \"integer\"",
            "é".repeat(1_000)
        );

        let kept = shortened(&detail);

        assert_eq!(kept.chars().count(), DETAIL_LIMIT_CHARS + 1);
        assert!(kept.starts_with("at /n: \"éé"));
        assert!(kept.ends_with('…'));
    }

    #[test]
    fn a_short_detail_is_kept_whole() {
        let detail = "at /n: \"abc\" is not of type \"integer\"";

        assert_eq!(shortened(detail), detail);
    }
}
