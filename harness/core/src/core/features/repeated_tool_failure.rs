//! Stops a turn whose model keeps repeating a tool call that fails the same way.
//!
//! A model that reads the same failure for the same call can issue that call again and again
//! until the context or the session runs out. When the host sets
//! `settings.turn.max_identical_tool_failures`, Core completes the turn once that many tool calls
//! in a row failed the same call, with the same arguments and the same result.
//!
//! Each failed call counts, including identical parallel calls in one reply, so a model cannot
//! run more identical failures than the limit by sending them in fewer replies. Core checks the
//! count when a reply's tool batch finishes: every call of that reply has run, and the turn stops
//! before the model is asked again.
//!
//! The count comes from the model context, not from separate state, so a checkpoint restore keeps
//! it.

use serde_json::Value;

use crate::core::features::large_output::same_for_the_model;
use crate::core::model_context::ModelContext;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{
    AssistantPart, AssistantSemanticPart, Message, ToolArguments, ToolOutcome,
};

/// The Turn's output when Core stops it.
pub(crate) const STOP_MESSAGE: &str = "Stopped this turn because the same tool call failed repeatedly. Send a new message to try a different approach.";

/// One failed call: what was called, with which arguments, and what the model read back.
struct Failure<'a> {
    tool_name: &'a str,
    arguments: Arguments<'a>,
    content: &'a [ContentBlock],
}

impl Failure<'_> {
    /// The same call, with the same arguments, read back the same way by the model.
    fn same_as(&self, other: &Self) -> bool {
        self.tool_name == other.tool_name
            && self.arguments == other.arguments
            && self.content.len() == other.content.len()
            && self
                .content
                .iter()
                .zip(other.content)
                .all(|(a, b)| same_for_the_model(a, b))
    }
}

/// Parsed arguments compare by value, so key order and spacing do not matter.
#[derive(PartialEq)]
enum Arguments<'a> {
    Json(&'a Value),
    InvalidJson(&'a str),
}

/// Whether the newest `limit` tool calls each failed the same call the same way.
///
/// Reads newest first, one call at a time, across replies. A success, a different failure, or
/// any message that is not a reply or a tool result, such as the turn's user message, steering, a
/// notification or a compaction summary, ends the run.
pub(crate) fn limit_reached(context: &ModelContext, limit: u32) -> bool {
    let mut newest: Option<Failure<'_>> = None;
    let mut failures = 0;
    let mut results = Vec::new();
    for stored in context.messages().iter().rev() {
        match &stored.message {
            Message::Tool {
                tool_call_id,
                name,
                outcome,
                content,
                ..
            } => results.push((
                tool_call_id.as_str(),
                name.as_str(),
                *outcome,
                content.as_slice(),
            )),
            Message::Assistant { content } if !results.is_empty() => {
                // The results of this reply's calls, newest first.
                for (call_id, tool_name, outcome, result) in results.drain(..) {
                    if outcome == ToolOutcome::Success {
                        return false;
                    }
                    let Some(arguments) = call_arguments(content, call_id) else {
                        return false;
                    };
                    let failure = Failure {
                        tool_name,
                        arguments,
                        content: result,
                    };
                    match &newest {
                        Some(newest) if !newest.same_as(&failure) => return false,
                        Some(_) => {}
                        None => newest = Some(failure),
                    }
                    failures += 1;
                    if failures >= limit {
                        return true;
                    }
                }
            }
            _ => return false,
        }
    }
    false
}

fn call_arguments<'a>(content: &'a [AssistantPart], call_id: &str) -> Option<Arguments<'a>> {
    content.iter().find_map(|part| match part {
        AssistantPart::Semantic(AssistantSemanticPart::ToolCall { id, arguments, .. })
            if id == call_id =>
        {
            Some(match arguments {
                ToolArguments::Json { value, .. } => Arguments::Json(value),
                ToolArguments::InvalidJson { raw, .. } => Arguments::InvalidJson(raw),
            })
        }
        _ => None,
    })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::core::features::large_output::{Policy, ResultDisposition, evaluate_result};
    use crate::core::wire::message::StoredMessage;
    use crate::core::wire::tool::{ProtocolError, ToolResult};

    const LIMIT: u32 = 3;

    /// What the model reads when `bash` fails with `output`, which Core saves as a large output
    /// to `saved_to`, or fails to save when `saved_to` is `None`. The excerpt keeps enough of the
    /// output to tell two outputs apart.
    fn large_failure(call_id: &str, output: &str, saved_to: Option<&str>) -> Vec<ContentBlock> {
        let policy = Policy::Filesystem {
            max_output_tokens: 100,
            model_visible_output_tokens: 100,
        };
        let failure: ToolResult = serde_json::from_value(json!({
            "type": "failure",
            "content": [{"type": "text", "text": output}],
            "error": {"code": "command_failed", "message": "Command failed. Return code: 1", "retryable": false, "details": null},
        }))
        .unwrap();
        let Some(ResultDisposition::Write(pending)) =
            evaluate_result(&policy, "bash", call_id, failure)
                .unwrap()
                .map(|evaluated| evaluated.disposition)
        else {
            panic!("the output is large enough to save");
        };
        let received = match saved_to {
            Some(path) => pending.complete(path),
            None => pending.fail(&ProtocolError {
                code: "filesystem_write_failed".to_string(),
                message: "disk full".to_string(),
                retryable: false,
                details: Value::Null,
            }),
        };
        received.content().to_vec()
    }

    /// A turn in which the model ran `pytest` once per output, oldest first, and each run failed
    /// with that output, which Core saved to a file named after the call, or failed to save.
    fn turn_with_failures(outputs: &[&str], saved: bool) -> ModelContext {
        let mut messages = vec![Message::user_text("run the tests")];
        for (attempt, output) in outputs.iter().enumerate() {
            let call_id = format!("call-{attempt}");
            let path = format!("/home/user/tool-results/bash-{call_id}.json");
            let content = large_failure(&call_id, output, saved.then_some(path.as_str()));
            messages.push(Message::assistant(vec![AssistantPart::Semantic(
                AssistantSemanticPart::ToolCall {
                    id: call_id.clone(),
                    name: "bash".to_string(),
                    arguments: ToolArguments::Json {
                        raw: r#"{"command":"pytest"}"#.to_string(),
                        value: json!({"command": "pytest"}),
                    },
                    meta: None,
                },
            )]));
            messages.push(Message::tool_failure(call_id, "bash".to_string(), content));
        }
        ModelContext::new(messages.into_iter().map(StoredMessage::visible).collect())
    }

    ///
    /// *Prepare*: `pytest` failed three times with a large output, which Core saved, or failed to
    /// save, to a file named after each call.
    /// *Do*: Check the limit of three.
    /// *Assert*: Receipts that differ only by their file count as the same failure. A receipt
    /// whose output differs does not, even when it is the oldest one.
    ///
    #[test]
    fn large_failures_compare_without_the_file_of_their_call() {
        let same = "x".repeat(400);
        let different = "y".repeat(400);
        let unsaved = "x".repeat(100_001);
        for (case, outputs, saved, expected) in [
            ("saved, same output", [&same, &same, &same], true, true),
            (
                "save failed, same output",
                [&unsaved, &unsaved, &unsaved],
                false,
                true,
            ),
            (
                "saved, oldest output differs",
                [&different, &same, &same],
                true,
                false,
            ),
        ] {
            // Prepare
            let context = turn_with_failures(&outputs.map(String::as_str), saved);

            // Do
            let reached = limit_reached(&context, LIMIT);

            // Assert
            assert_eq!(reached, expected, "{case}");
        }
    }
}
