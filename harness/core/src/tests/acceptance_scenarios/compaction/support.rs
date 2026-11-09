//! Builders and matchers the compaction scenarios share.

use super::*;

pub(super) fn automatic_compaction_config() -> HarnessConfig {
    automatic_compaction_config_with_threshold(40_000)
}

/// Automatic compaction at `token_threshold` on a model whose window is just
/// above it. The summary request budget is the threshold, and a request
/// refused near the threshold grows the estimate correction by the 4/3 floor
/// rather than by window ÷ refused estimate.
pub(super) fn automatic_compaction_config_with_threshold(token_threshold: u64) -> HarnessConfig {
    let mut value = serde_json::to_value(config()).expect("config serializes");
    value["settings"]["context"]["compaction"] = json!({ "mode": "automatic" });
    let facts = &mut value["settings"]["context"]["model_facts"];
    facts["compaction_threshold"] = json!(token_threshold);
    facts["compaction_threshold_max"] = json!(token_threshold);
    facts["context_window"] = json!(token_threshold + 1);
    serde_json::from_value(value).expect("automatic compaction config is valid")
}

/// An image cost above the 40,000-token test threshold, so one native image
/// cannot fit a request on its own.
pub(super) const LARGE_IMAGE_TOKENS: u64 = 50_000;

pub(super) fn automatic_compaction_config_with_image_cost(
    token_threshold: u64,
    image_tokens: u64,
) -> HarnessConfig {
    let mut value =
        serde_json::to_value(automatic_compaction_config_with_threshold(token_threshold))
            .expect("config serializes");
    value["settings"]["context"]["model_facts"]["image_cost"] =
        json!({ "kind": "fixed", "tokens": image_tokens });
    serde_json::from_value(value).expect("image cost config is valid")
}

pub(super) fn completion_failed(action: &Value, code: &str, message: &str) -> Value {
    json!({
        "type": "completion_failed",
        "action_id": action_id(action),
        "error": {
            "code": code,
            "message": message,
            "retryable": false,
            "details": null,
        },
    })
}

pub(super) fn summary_completion(action: &Value, summary: &str) -> Value {
    text_completion(action, &format!("<summary>{summary}</summary>"))
}

pub(super) fn length_completion(action: &Value, text: &str) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id(action),
        "result": {
            "parts": [{"type": "text", "text": text}],
            "finish_reason": "length",
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0},
        },
    })
}

/// Checks the standalone request shape: a summarizer system prompt and one
/// user message carrying the transcript, with no agent system prompt.
pub(super) fn assert_standalone_request(action: &Value, transcript_text: &str) {
    let messages = action_messages(action);
    assert_eq!(messages.len(), 2, "standalone request has two messages");
    assert_eq!(messages[0]["role"], "system");
    assert!(
        message_text(&messages[0])
            .is_some_and(|text| text.starts_with("You are a dedicated conversation summarizer."))
    );
    assert_eq!(messages[1]["role"], "user");
    let user = message_text(&messages[1]).expect("standalone request has text");
    assert!(user.starts_with("<conversation_transcript>\nUserMessage: "));
    assert!(user.contains(transcript_text));
    assert!(user.contains("Write the handoff summary for the conversation transcript above."));
    assert!(!user.contains("CONTEXT CHECKPOINT COMPACTION"));
}

pub(super) fn reasoning_only_completion(action: &Value) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id(action),
        "result": {
            "parts": [{
                "type": "reasoning",
                "content": [{"type": "text", "text": "internal reasoning"}],
            }],
            "finish_reason": "stop",
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0},
        },
    })
}

pub(super) fn summary_with_tool_call_completion(action: &Value, summary: &str) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id(action),
        "result": {
            "parts": [
                {"type": "text", "text": format!("<summary>{summary}</summary>")},
                tool_call("unexpected-tool", "read_file", json!({"path": "notes.txt"})),
            ],
            "finish_reason": "tool_call",
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0},
        },
    })
}

pub(super) fn compact(extra_instructions: &str) -> Value {
    json!({
        "type": "compact",
        "extra_instructions": extra_instructions,
    })
}

pub(super) fn context_compacted(turn_id: &str, action: &Value, summary: &str) -> Value {
    json!({
        "type": "context_compacted",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "compaction_id": action["compaction_id"],
        "attempt": action["attempt"],
        "trigger": "automatic",
        "summary": summary,
        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0},
    })
}

pub(super) fn context_compaction_failed(turn_id: &str, action: &Value, error: Value) -> Value {
    json!({
        "type": "context_compaction_failed",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "compaction_id": action["compaction_id"],
        "attempt": action["attempt"],
        "trigger": "automatic",
        "error": error,
    })
}

pub(super) fn manual_context_compaction_failed(action: &Value, error: Value) -> Value {
    json!({
        "type": "context_compaction_failed",
        "turn_id": null,
        "action_id": action_id(action),
        "compaction_id": action["compaction_id"],
        "attempt": action["attempt"],
        "trigger": "manual",
        "error": error,
    })
}

pub(super) fn failed_candidate(turn_id: &str, action: &Value, error: Value) -> Value {
    json!({
        "type": "agent_completion_candidate_discarded",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "cause": {"type": "failure", "error": error},
    })
}

pub(super) fn turn_failed(turn_id: &str, error: Value) -> Value {
    json!({"type": "turn_failed", "turn_id": turn_id, "error": error})
}

pub(super) const REDUCED_VIEW_HEADER: &str =
    "<system>Items after the summary, shown reduced because they did not fit:</system>";
pub(super) const REDUCED_VIEW_OMITTED: &str =
    "<system>Items after the summary happened here and were omitted for size.</system>";

/// The warning Core sends when the rebuilt history shows items after the
/// summary reduced.
pub(super) fn inputs_reduced(turn_id: &str, reduced_messages: usize, view: &str) -> Value {
    json!({
        "type": "warning",
        "turn_id": turn_id,
        "code": "compaction_inputs_reduced",
        "message": "The history rebuilt after compaction did not fit the context and was reduced.",
        "details": {
            "reduced_messages": reduced_messages,
            "view": view,
        },
    })
}

/// The note after a summary cut to fit.
pub(super) const SUMMARY_CUT_NOTE: &str =
    "<system>Summary was too long and has been cut off</system>";

/// The warning Core sends when it keeps a summary cut to fit, with the note:
/// one cut off at the output limit after its retry, or one too long for the
/// rebuilt history.
pub(super) fn summary_cut_off(
    turn_id: &str,
    cause: &str,
    summary_bytes: usize,
    kept_bytes: usize,
) -> Value {
    let message = match cause {
        "cut_off" => {
            "The compaction summary was cut off at the output limit; the part written is kept, \
             with a note that it was cut."
        }
        _ => {
            "The compaction summary was too long for the rebuilt history and was cut, with a \
             note that it was cut."
        }
    };
    json!({
        "type": "warning",
        "turn_id": turn_id,
        "code": "compaction_summary_cut_off",
        "message": message,
        "details": {
            "cause": cause,
            "summary_bytes": summary_bytes,
            "kept_bytes": kept_bytes,
        },
    })
}

/// How many bytes of the summary Core keeps when `command` makes it cut one,
/// read from a preview of the transition: Core picks the line end.
pub(super) fn previewed_kept_bytes(runtime: &SynchronousRuntime, command: &Value) -> usize {
    let observations = runtime.preview_observations(command.clone());
    let warning = observations
        .as_array()
        .expect("observations are a list")
        .iter()
        .find(|observation| observation["code"] == "compaction_summary_cut_off")
        .expect("Core cuts the summary");
    warning["details"]["kept_bytes"]
        .as_u64()
        .and_then(|bytes| usize::try_from(bytes).ok())
        .expect("the warning counts the kept bytes")
}

/// The marker a user message truncated to fit holds in place of its middle.
pub(super) const TRUNCATION_MARKER_END: &str = " tokens truncated here for size</system>";

pub(super) fn action_messages(action: &Value) -> &[Value] {
    action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("completion action has model messages")
}

pub(super) fn message_text(message: &Value) -> Option<&str> {
    message["content"]
        .as_array()
        .and_then(|content| content.first())
        .and_then(|content| content["text"].as_str())
}

pub(super) fn large_text(label: &str) -> String {
    format!("{label} {}", "0123456789abcdef".repeat(12_000))
}

/// A runtime whose next turn needs compaction: an older exchange too large for
/// the threshold, then `request` and a reply. The in-context request fits once
/// the older exchange is removed.
pub(super) fn runtime_needing_compaction(request: &str) -> SynchronousRuntime {
    SynchronousRuntime::new_with_history(
        automatic_compaction_config(),
        vec![
            Message::user_text(large_text("old request")),
            assistant_text("old answer"),
            Message::user_text(request),
            assistant_text("working on it"),
        ],
    )
}

/// Queues the turn "continue" and returns the in-context compaction Action
/// that the check sends before it.
pub(super) fn start_continued_turn(runtime: &mut SynchronousRuntime, turn_id: &str) -> Value {
    let compaction = runtime
        .apply(
            user_message(turn_id, "continue", "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, "continue")),
        )
        .only_action();
    assert!(compaction.get("request").is_none(), "{compaction}");
    compaction
}

pub(super) fn user_message_with_content(turn_id: &str, content: Value) -> Value {
    user_message_with_content_and_mode(turn_id, content, "queue")
}

pub(super) fn user_message_with_content_and_mode(
    turn_id: &str,
    content: Value,
    mode: &str,
) -> Value {
    json!({
        "type": "user_message",
        "turn_id": turn_id,
        "mode": mode,
        "content": content,
    })
}

pub(super) fn turn_started_with_content(turn_id: &str, content: Value) -> Value {
    json!({
        "type": "turn_started",
        "turn_id": turn_id,
        "content": content,
    })
}

/// An image the app saved to a file, as a standard embedded resource whose
/// base64 data alone is about 50,000 tokens, past the 40,000-token threshold.
pub(super) fn file_image_content() -> Value {
    json!([{
        "type": "resource",
        "resource": {
            "uri": "file:///workspace/screenshot.png",
            "mimeType": "image/png",
            "blob": "a".repeat(200_000),
        },
    }])
}

pub(super) const FILE_IMAGE_PLACEHOLDER: &str =
    "[Image] [resource: file:///workspace/screenshot.png]";

pub(super) fn text_only(mut config: HarnessConfig) -> HarnessConfig {
    config.settings.context.model_facts.input_modalities = vec![InputModality::Text];
    config
}

pub(super) fn with_text_only_compaction_model(mut config: HarnessConfig) -> HarnessConfig {
    config.settings.context.compaction_model_input_modalities = Some(vec![InputModality::Text]);
    config
}

pub(super) fn action_text_blocks(action: &Value) -> Vec<&str> {
    action_messages(action)
        .iter()
        .flat_map(|message| message["content"].as_array().into_iter().flatten())
        .filter_map(|block| block["text"].as_str())
        .collect()
}

pub(super) fn first_action_block_with_type<'a>(action: &'a Value, block_type: &str) -> &'a Value {
    action_messages(action)
        .iter()
        .flat_map(|message| message["content"].as_array().into_iter().flatten())
        .find(|block| block["type"] == block_type)
        .unwrap_or_else(|| panic!("completion action has no {block_type} content block"))
}

/// Starts a turn on `history` that calls `read_file` and returns the tool
/// action.
pub(super) fn read_large_file(runtime: &mut SynchronousRuntime, turn_id: &str) -> Value {
    let request = "read the large file";
    let first_agent = runtime
        .apply(
            user_message(turn_id, request, "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, request)),
        )
        .only_action();
    let arguments = json!({"path": "large.txt"});
    let calls = vec![tool_call("large-read", "read_file", arguments.clone())];
    runtime
        .complete_with_tool_calls(
            turn_id,
            &first_agent,
            &calls,
            [runtime_tool("file_system.read_file", arguments)],
        )
        .only_action()
}

pub(super) const TOOL_RESULT_PLACEHOLDER: &str =
    "<system>The result of this call is kept outside this summary request.</system>";

/// Checks a cut-off retry: the standalone request with the instruction to
/// answer briefly as one more user message.
pub(super) fn assert_cut_off_retry_request(action: &Value, transcript_text: &str) {
    let messages = action_messages(action);
    assert_eq!(
        messages.len(),
        3,
        "a cut-off retry adds one message: {action}"
    );
    let standalone = json!({"model_input": {"messages": {"messages": messages[..2].to_vec()}}});
    assert_standalone_request(&standalone, transcript_text);
    assert_eq!(messages[2]["role"], "user");
    assert_eq!(
        message_text(&messages[2]),
        Some("You were cut off. Don't think, don't be long, just answer.")
    );
}
