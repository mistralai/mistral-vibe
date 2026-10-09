use super::*;
use crate::core::testing::{add_always_hook, assistant_text, compaction_action_id};
use crate::core::{ImageDeliveryMode, Message};

fn automatic_compaction_config() -> HarnessConfig {
    automatic_compaction_config_with_threshold(40_000)
}

fn automatic_compaction_config_with_threshold(token_threshold: u64) -> HarnessConfig {
    let mut value = serde_json::to_value(config()).expect("config serializes");
    value["settings"]["context"]["compaction"] = json!({
        "mode": "automatic",
        "token_threshold": token_threshold,
    });
    serde_json::from_value(value).expect("automatic compaction config is valid")
}

fn completion_failed(action: &Value, code: &str, message: &str) -> Value {
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

fn summary_completion(action: &Value, summary: &str) -> Value {
    text_completion(action, &format!("<summary>{summary}</summary>"))
}

fn reasoning_only_completion(action: &Value) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id(action),
        "result": {
            "parts": [{
                "type": "reasoning",
                "content": [{"type": "text", "text": "internal reasoning"}],
            }],
            "finish_reason": "stop",
            "usage": null,
        },
    })
}

fn summary_with_tool_call_completion(action: &Value, summary: &str) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id(action),
        "result": {
            "parts": [
                {"type": "text", "text": format!("<summary>{summary}</summary>")},
                tool_call("unexpected-tool", "read_file", json!({"path": "notes.txt"})),
            ],
            "finish_reason": "tool_call",
            "usage": null,
        },
    })
}

fn compact(extra_instructions: &str) -> Value {
    json!({
        "type": "compact",
        "extra_instructions": extra_instructions,
    })
}

fn context_compacted(turn_id: &str, action: &Value, summary: &str) -> Value {
    json!({
        "type": "context_compacted",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "compaction_id": action["compaction_id"],
        "attempt": action["attempt"],
        "trigger": "automatic",
        "summary": summary,
        "usage": null,
    })
}

fn context_compaction_failed(turn_id: &str, action: &Value, error: Value) -> Value {
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

fn manual_context_compaction_failed(action: &Value, error: Value) -> Value {
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

fn failed_candidate(turn_id: &str, action: &Value, error: Value) -> Value {
    json!({
        "type": "agent_completion_candidate_discarded",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "cause": {"type": "failure", "error": error},
    })
}

fn turn_failed(turn_id: &str, error: Value) -> Value {
    json!({"type": "turn_failed", "turn_id": turn_id, "error": error})
}

fn action_messages(action: &Value) -> &[Value] {
    action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("completion action has model messages")
}

fn message_text(message: &Value) -> Option<&str> {
    message["content"]
        .as_array()
        .and_then(|content| content.first())
        .and_then(|content| content["text"].as_str())
}

fn large_text(label: &str) -> String {
    format!("{label} {}", "0123456789abcdef".repeat(12_000))
}

const PRE_LLM_BINDING_IDS: [&str; 1] = ["test-hook-0-PreLlmCall"];

/// Starts a turn whose pre-LLM hook returns `github` context. Checks that the
/// turn fails before a compaction call because no replacement can hold it.
fn fail_turn_with_unfittable_context(
    runtime: &mut SynchronousRuntime,
    compaction_id: &str,
    turn_id: &str,
    context: &str,
) {
    let error = json!({
        "code": "compaction_replacement_too_large",
        "message": "compaction summary and required continuation context cannot fit the configured token threshold",
        "retryable": false,
        "details": null,
    });
    let hook = runtime
        .apply(
            user_message(turn_id, "inspect the repository", "queue"),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &PRE_LLM_BINDING_IDS, Value::Null))
                .observe(turn_started(turn_id, "inspect the repository")),
        )
        .only_action();
    runtime.apply(
        hook_completed(
            &hook,
            "pre_llm_call",
            pre_llm_continue_with_context(&[("github", context)]),
        ),
        failed(turn_id, error.clone())
            .observe(json!({
                "type": "context_compaction_failed",
                "turn_id": turn_id,
                "action_id": compaction_id,
                "compaction_id": compaction_id,
                "attempt": 1,
                "trigger": "automatic",
                "error": error.clone(),
            }))
            .observe(turn_failed(turn_id, error)),
    );
}

/// Starts a turn whose pre-LLM hook returns `output` before and after an
/// automatic compaction. Returns the agent completion that follows.
fn compact_turn_after_pre_llm_hook(
    runtime: &mut SynchronousRuntime,
    turn_id: &str,
    output: Value,
) -> Value {
    let first_hook = runtime
        .apply(
            user_message(turn_id, "inspect it again", "queue"),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &PRE_LLM_BINDING_IDS, Value::Null))
                .observe(turn_started(turn_id, "inspect it again")),
        )
        .only_action();
    let compaction = runtime
        .apply(
            hook_completed(&first_hook, "pre_llm_call", output.clone()),
            running(turn_id).dispatch(compaction_call("automatic", 0, 1)),
        )
        .only_action();
    let second_hook = runtime
        .apply(
            summary_completion(&compaction, "repository summary"),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &PRE_LLM_BINDING_IDS, Value::Null))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "repository summary",
                )),
        )
        .only_action();
    runtime
        .apply(
            hook_completed(&second_hook, "pre_llm_call", output),
            running(turn_id).dispatch(llm_call(0)),
        )
        .only_action()
}

fn model_texts(action: &Value) -> Vec<&str> {
    action_messages(action)
        .iter()
        .filter_map(message_text)
        .collect()
}

fn user_message_with_content(turn_id: &str, content: Value) -> Value {
    user_message_with_content_and_mode(turn_id, content, "queue")
}

fn user_message_with_content_and_mode(turn_id: &str, content: Value, mode: &str) -> Value {
    json!({
        "type": "user_message",
        "turn_id": turn_id,
        "mode": mode,
        "content": content,
    })
}

fn turn_started_with_content(turn_id: &str, content: Value) -> Value {
    json!({
        "type": "turn_started",
        "turn_id": turn_id,
        "content": content,
    })
}

fn file_image_content() -> Value {
    json!([{
        "type": "image",
        "data": "a".repeat(200_000),
        "mimeType": "image/png",
        "_meta": {
            "mistralai.vibe.harness/file-image-resource-link": {
                "type": "resource_link",
                "uri": "file:///workspace/screenshot.png",
                "name": "screenshot.png",
                "mimeType": "image/png",
                "size": 150_000,
            },
        },
    }])
}

fn first_action_block_with_type<'a>(action: &'a Value, block_type: &str) -> &'a Value {
    action_messages(action)
        .iter()
        .flat_map(|message| message["content"].as_array().into_iter().flatten())
        .find(|block| block["type"] == block_type)
        .unwrap_or_else(|| panic!("completion action has no {block_type} content block"))
}

#[test]
fn preserves_stored_message_fields_in_completion_actions() {
    let turn_id = "turn-content-metadata";
    let content = json!([{
        "type": "resource_link",
        "uri": "https://example.test/image.png",
        "name": "image.png",
        "annotations": {"audience": ["assistant"]},
        "_meta": {"provider.example/materialization": {"type": "image_url"}},
    }]);
    let assistant_message = json!({
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "answer",
                "annotations": {"audience": ["assistant"]},
                "_meta": {"provider.example/content": "content-metadata"},
            },
            {
                "type": "reasoning",
                "content": [{"type": "text", "text": "thinking"}],
                "_meta": {"provider.example/reasoning": "reasoning-metadata"},
            },
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "lookup",
                "arguments": {"type": "json", "raw": "{}", "value": {}},
                "_meta": {"provider.example/tool-call": "tool-call-metadata"},
            },
        ],
    });
    let tool_message = json!({
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "lookup",
        "outcome": "success",
        "content": [{
            "type": "text",
            "text": "result",
            "_meta": {"provider.example/content": "tool-content-metadata"},
        }],
        "_meta": {"provider.example/tool-message": "tool-message-metadata"},
    });
    let history = [&assistant_message, &tool_message]
        .into_iter()
        .map(|message| serde_json::from_value(message.clone()).expect("history message is valid"))
        .collect();
    let mut runtime = SynchronousRuntime::new_with_history(config(), history);

    let action = runtime
        .apply(
            user_message_with_content(turn_id, content.clone()),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(turn_id, content.clone())),
        )
        .only_action();

    let messages = action_messages(&action);
    assert!(messages.contains(&assistant_message));
    assert!(messages.contains(&tool_message));
    assert!(messages.contains(&json!({"role": "user", "content": content})));
}

#[test]
fn preserves_assistant_resource_link_metadata_in_completion_actions() {
    let turn_id = "turn-assistant-resource-link-metadata";
    let assistant_message = json!({
        "role": "assistant",
        "content": [{
            "type": "resource_link",
            "uri": "provider://references",
            "name": "references",
            "_meta": {"provider.example/materialization": {"type": "reference"}},
        }],
    });
    let history =
        vec![serde_json::from_value(assistant_message.clone()).expect("history message is valid")];
    let mut runtime = SynchronousRuntime::new_with_history(config(), history);

    let action = runtime
        .apply(
            user_message(turn_id, "use the previous references", "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "use the previous references")),
        )
        .only_action();

    assert!(action_messages(&action).contains(&assistant_message));
}

#[test]
fn oversized_agent_input_compacts_before_any_agent_provider_call() {
    let turn_id = "turn-preflight-compaction";
    let request = large_text("current request");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());

    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    assert_eq!(compaction["purpose"], "compaction");
    assert_eq!(compaction["model_input"]["messages"]["type"], "replace");
    let projected_request = action_messages(&compaction)
        .iter()
        .filter_map(message_text)
        .find(|text| text.contains("current request"))
        .expect("compaction must preserve a fitted copy of the latest user request");
    assert!(projected_request.len() < request.len());
    assert!(projected_request.contains("tokens truncated"));

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "preserve the current request"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "preserve the current request",
                )),
        )
        .only_action();
    assert_eq!(resumed["purpose"], "agent");
    assert_eq!(resumed["model_input"]["messages"]["type"], "replace");
    let resumed_request = action_messages(&resumed)
        .iter()
        .filter_map(message_text)
        .find(|text| text.contains("current request"))
        .expect("replacement context must preserve the latest user request");
    assert!(resumed_request.len() <= 20_000 * 4);
}

#[test]
fn model_input_budget_ignores_content_annotations_and_metadata() {
    let cases = [
        json!({
            "type": "text",
            "text": "small request",
            "annotations": {"audience": vec!["assistant"; 30_000]},
        }),
        json!({
            "type": "text",
            "text": "small request",
            "_meta": {"padding": "x".repeat(300_000)},
        }),
    ];

    for (index, block) in cases.into_iter().enumerate() {
        let turn_id = format!("turn-model-visible-budget-{index}");
        let content = json!([block]);
        let mut runtime = SynchronousRuntime::new(automatic_compaction_config());

        let action = runtime
            .apply(
                user_message_with_content(&turn_id, content.clone()),
                running(&turn_id)
                    .dispatch(llm_call(0))
                    .observe(turn_started_with_content(&turn_id, content)),
            )
            .only_action();

        assert_eq!(action["purpose"], "agent");
    }
}

#[test]
fn file_link_image_delivery_budgets_the_provider_projection() {
    let turn_id = "turn-file-link-image-budget";
    let mut value = serde_json::to_value(automatic_compaction_config()).expect("config serializes");
    value["settings"]["context"]["image_delivery"] = json!({
        "agent": "resource_link",
        "compaction": "resource_link",
    });
    let config = serde_json::from_value(value).expect("file-link image delivery config is valid");
    let content = file_image_content();
    let mut runtime = SynchronousRuntime::new(config);

    let action = runtime
        .apply(
            user_message_with_content(turn_id, content.clone()),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(turn_id, content)),
        )
        .only_action();

    assert_eq!(action["purpose"], "agent");
    let resource_link = first_action_block_with_type(&action, "resource_link");
    assert_eq!(resource_link["uri"], "file:///workspace/screenshot.png");
    assert!(resource_link.get("data").is_none());
    assert!(resource_link.get("_meta").is_none());
}

#[test]
fn compaction_uses_a_file_link_then_resumes_the_agent_with_the_native_image() {
    let turn_id = "turn-compaction-file-link-budget";
    let mut value = serde_json::to_value(automatic_compaction_config_with_threshold(80_000))
        .expect("config serializes");
    value["settings"]["context"]["image_delivery"] = json!({
        "agent": "native",
        "compaction": "resource_link",
    });
    let config = serde_json::from_value(value).expect("file-link image delivery config is valid");
    let content = file_image_content();
    let mut runtime = SynchronousRuntime::new_with_history(
        config,
        vec![
            Message::user_text(large_text("old request")),
            assistant_text("old answer"),
        ],
    );

    let action = runtime
        .apply(
            user_message_with_content(turn_id, content.clone()),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started_with_content(turn_id, content)),
        )
        .only_action();

    assert_eq!(action["purpose"], "compaction");
    let resource_link = first_action_block_with_type(&action, "resource_link");
    assert_eq!(resource_link["uri"], "file:///workspace/screenshot.png");
    assert!(resource_link.get("data").is_none());

    let summary = "preserve the latest image";
    let resumed = runtime
        .apply(
            summary_completion(&action, summary),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &action, summary)),
        )
        .only_action();

    assert_eq!(resumed["purpose"], "agent");
    assert_eq!(resumed["model_input"]["messages"]["type"], "replace");
    let image = first_action_block_with_type(&resumed, "image");
    assert_eq!(image["data"].as_str().map(str::len), Some(200_000));
    assert!(
        image["_meta"]
            .get("mistralai.vibe.harness/file-image-resource-link")
            .is_some()
    );
}

#[test]
fn impossible_native_replacement_fails_before_file_link_compaction_call() {
    let turn_id = "turn-unfittable-native-image-replacement";
    let mut value = serde_json::to_value(automatic_compaction_config()).expect("config serializes");
    value["settings"]["context"]["image_delivery"] = json!({
        "agent": "native",
        "compaction": "resource_link",
    });
    let config: HarnessConfig =
        serde_json::from_value(value).expect("mixed image delivery config is valid");
    let compaction_id = compaction_action_id(&config.task_id, 1);
    let content = file_image_content();
    let error = json!({
        "code": "compaction_replacement_too_large",
        "message": "compaction summary and required continuation context cannot fit the configured token threshold",
        "retryable": false,
        "details": null,
    });
    let mut runtime = SynchronousRuntime::new(config);

    runtime.apply(
        user_message_with_content(turn_id, content.clone()),
        failed(turn_id, error.clone())
            .observe(turn_started_with_content(turn_id, content))
            .observe(json!({
                "type": "context_compaction_failed",
                "turn_id": turn_id,
                "action_id": compaction_id,
                "compaction_id": compaction_id,
                "attempt": 1,
                "trigger": "automatic",
                "error": error.clone(),
            }))
            .observe(turn_failed(turn_id, error)),
    );
}

#[test]
fn changing_agent_image_delivery_replaces_the_runtime_message_cache() {
    let first_turn_id = "turn-file-link-before-model-switch";
    let mut config = config();
    config.settings.context.image_delivery.agent = ImageDeliveryMode::ResourceLink;
    let content = file_image_content();
    let mut runtime = SynchronousRuntime::new(config.clone());

    let first_action = runtime
        .apply(
            user_message_with_content(first_turn_id, content.clone()),
            running(first_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(first_turn_id, content)),
        )
        .only_action();
    assert_eq!(first_action["model_input"]["messages"]["type"], "replace");
    assert_eq!(
        first_action_block_with_type(&first_action, "resource_link")["uri"],
        "file:///workspace/screenshot.png"
    );
    runtime.finish_turn_with_text(first_turn_id, &first_action, "done");

    config.settings.context.image_delivery.agent = ImageDeliveryMode::Native;
    runtime.reconfigure_settings(
        config.settings,
        completed(first_turn_id, vec![json!({"type": "text", "text": "done"})]),
    );
    let second_turn_id = "turn-native-after-model-switch";
    let second_action = runtime
        .apply(
            user_message(second_turn_id, "inspect it again", "queue"),
            running(second_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(second_turn_id, "inspect it again")),
        )
        .only_action();

    assert_eq!(second_action["model_input"]["messages"]["type"], "replace");
    let image = first_action_block_with_type(&second_action, "image");
    assert_eq!(image["data"].as_str().map(str::len), Some(200_000));
    assert!(
        image["_meta"]
            .get("mistralai.vibe.harness/file-image-resource-link")
            .is_some()
    );
    let checkpoint = runtime.checkpoint_value();
    let stored_image = checkpoint["context"]["messages"]
        .as_array()
        .expect("checkpoint has model messages")
        .iter()
        .flat_map(|stored| {
            stored["message"]["content"]
                .as_array()
                .into_iter()
                .flatten()
        })
        .find(|block| block["type"] == "image")
        .expect("checkpoint preserves the canonical image");
    assert!(
        stored_image["_meta"]
            .get("mistralai.vibe.harness/file-image-resource-link")
            .is_some()
    );
}

#[test]
fn keeps_agent_message_cache_when_only_compaction_image_delivery_changes() {
    let first_turn_id = "turn-before-compaction-model-switch";
    let mut config = config();
    let mut runtime = SynchronousRuntime::new(config.clone());
    let first_action = runtime.start_turn(first_turn_id, "first request");
    runtime.finish_turn_with_text(first_turn_id, &first_action, "done");

    config.settings.context.image_delivery.compaction = ImageDeliveryMode::ResourceLink;
    runtime.reconfigure_settings(
        config.settings,
        completed(first_turn_id, vec![json!({"type": "text", "text": "done"})]),
    );
    let second_turn_id = "turn-after-compaction-model-switch";
    let second_action = runtime
        .apply(
            user_message(second_turn_id, "second request", "queue"),
            running(second_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(second_turn_id, "second request")),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &second_action,
        "append",
        &[
            model_assistant_text("done"),
            model_user_text("second request"),
        ],
    );
}

#[test]
fn oversized_tool_result_compacts_before_the_next_agent_provider_call() {
    let turn_id = "turn-tool-result-preflight-compaction";
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config_with_threshold(20_000));
    let first_agent = runtime.start_turn(turn_id, "read the large file");
    let arguments = json!({"path": "large.txt"});
    let calls = vec![tool_call("large-read", "read_file", arguments.clone())];
    let tool_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_agent,
            &calls,
            [runtime_tool("file_system.read_file", arguments)],
        )
        .only_action();
    let tool_output = format!("large file contents {}", "0123456789abcdef".repeat(5_000));
    let tool_result = file_read_success(&tool_action, &tool_output);

    let compaction = runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(compaction_call("automatic", 1, 1))
                .observe_completed_tool_result(&tool_action, &tool_result),
        )
        .only_action();

    assert_eq!(compaction["purpose"], "compaction");
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "large file summary"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "large file summary",
                )),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn provider_reported_usage_over_threshold_triggers_compaction() {
    // Regression (VIBE-4298): the automatic-compaction trigger must react to the
    // provider-reported context size, not only the crude serialized-bytes
    // estimate (`bytes / APPROX_BYTES_PER_TOKEN`). For models whose tokenizer
    // counts far more tokens than that estimate (e.g. GLM-5.2), a tiny
    // conversation can still blow past the real context budget while the
    // estimate stays comfortably under it -- so compaction never fires even
    // though the status bar (fed by the provider usage) shows the overflow.
    let turn_id = "turn-provider-usage-over-threshold";
    let threshold = 20_000;
    let mut runtime =
        SynchronousRuntime::new(automatic_compaction_config_with_threshold(threshold));

    // A minimal turn whose serialized messages are nowhere near the threshold.
    let agent = runtime.start_turn(turn_id, "hi");

    // The provider reports an input context well above the threshold.
    let usage = json!({
        "input_tokens": threshold + 5_000,
        "output_tokens": 10,
        "total_tokens": threshold + 5_010,
        "cached_input_tokens": 0,
    });
    let completion = json!({
        "type": "completion_succeeded",
        "action_id": action_id(&agent),
        "result": {
            "parts": [{"type": "text", "text": "ok"}],
            "finish_reason": "stop",
            "usage": usage,
        },
    });
    let committed_candidate = json!({
        "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        "finish_reason": "stop",
        "usage": usage,
    });
    runtime.apply(
        completion,
        completed(turn_id, vec![json!({"type": "text", "text": "ok"})])
            .observe(json!({
                "type": "assistant_message_committed",
                "turn_id": turn_id,
                "action_id": action_id(&agent),
                "candidate": committed_candidate,
            }))
            .observe(turn_completed(turn_id, "ok")),
    );

    // Next turn: the messages are still tiny, but the provider already reported
    // that the live context exceeds the budget. The harness must compact before
    // the first agent provider call instead of dispatching an ordinary llm_call.
    let next_turn = "turn-provider-usage-over-threshold-2";
    let content = json!([{"type": "text", "text": "again"}]);
    let compaction = runtime
        .apply(
            user_message_with_content(next_turn, content.clone()),
            running(next_turn)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started_with_content(next_turn, content)),
        )
        .only_action();
    assert_eq!(compaction["purpose"], "compaction");

    // After compaction the reported size is cleared, so the resumed agent call
    // is dispatched normally instead of compacting again in a loop.
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(next_turn)
                .dispatch(llm_call(0))
                .observe(context_compacted(next_turn, &compaction, "summary")),
        )
        .only_action();
    assert_eq!(resumed["purpose"], "agent");
}

#[test]
fn failed_automatic_compaction_keeps_provider_reported_over_budget_signal() {
    // The serialized-bytes estimate stays under the threshold, so the only
    // over-budget signal is the last provider-reported size. Compaction must
    // keep that signal until it actually replaces the live context; otherwise a
    // failed attempt leaves the next turn with no reason to compact again.
    let turn_id = "turn-keep-reported-after-failed-compaction";
    let threshold = 20_000;
    let mut runtime =
        SynchronousRuntime::new(automatic_compaction_config_with_threshold(threshold));

    let agent = runtime.start_turn(turn_id, "hi");
    let usage = json!({
        "input_tokens": threshold + 5_000,
        "output_tokens": 10,
        "total_tokens": threshold + 5_010,
        "cached_input_tokens": 0,
    });
    let completion = json!({
        "type": "completion_succeeded",
        "action_id": action_id(&agent),
        "result": {
            "parts": [{"type": "text", "text": "ok"}],
            "finish_reason": "stop",
            "usage": usage,
        },
    });
    let committed_candidate = json!({
        "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        "finish_reason": "stop",
        "usage": usage,
    });
    runtime.apply(
        completion,
        completed(turn_id, vec![json!({"type": "text", "text": "ok"})])
            .observe(json!({
                "type": "assistant_message_committed",
                "turn_id": turn_id,
                "action_id": action_id(&agent),
                "candidate": committed_candidate,
            }))
            .observe(turn_completed(turn_id, "ok")),
    );

    // First over-budget turn compacts, then the summary provider fails.
    let failing_turn = "turn-keep-reported-after-failed-compaction-2";
    let failing_content = json!([{"type": "text", "text": "again"}]);
    let compaction = runtime
        .apply(
            user_message_with_content(failing_turn, failing_content.clone()),
            running(failing_turn)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started_with_content(failing_turn, failing_content)),
        )
        .only_action();
    assert_eq!(compaction["purpose"], "compaction");

    let error = json!({
        "code": "provider_error",
        "message": "summary provider failed",
        "retryable": false,
        "details": null,
    });
    runtime.apply(
        completion_failed(&compaction, "provider_error", "summary provider failed"),
        failed(failing_turn, error.clone())
            .observe(context_compaction_failed(
                failing_turn,
                &compaction,
                error.clone(),
            ))
            .observe(turn_failed(failing_turn, error)),
    );

    // The failed compaction never replaced the context, so the reported size is
    // still the only over-budget signal. The next turn must compact again.
    let retry_turn = "turn-keep-reported-after-failed-compaction-3";
    let retry_content = json!([{"type": "text", "text": "retry"}]);
    let retry_compaction = runtime
        .apply(
            user_message_with_content(retry_turn, retry_content.clone()),
            running(retry_turn)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started_with_content(retry_turn, retry_content)),
        )
        .only_action();
    assert_eq!(retry_compaction["purpose"], "compaction");
}

#[test]
fn unfittable_compaction_after_a_tool_result_fails_without_rolling_back_the_command() {
    let turn_id = "turn-unfittable-compaction";
    let config = automatic_compaction_config();
    let compaction_id = compaction_action_id(&config.task_id, 1);
    let mut runtime = SynchronousRuntime::new(config);
    let first_agent = runtime.start_turn(turn_id, "read the file");
    let arguments = json!({"path": "small.txt"});
    let calls = vec![tool_call("small-read", "read_file", arguments.clone())];
    let tool_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_agent,
            &calls,
            [runtime_tool("file_system.read_file", arguments)],
        )
        .only_action();
    let steering_content = json!([{
        "type": "image",
        "data": "a".repeat(200_000),
        "mimeType": "image/png",
    }]);

    runtime.apply(
        user_message_with_content_and_mode(turn_id, steering_content.clone(), "steer"),
        running(turn_id).keep(&tool_action).observe(json!({
            "type": "turn_steering_received",
            "turn_id": turn_id,
            "content": steering_content,
        })),
    );

    let tool_result = file_read_success(&tool_action, "small file contents");
    let error = json!({
        "code": "compaction_request_too_large",
        "message": "required compaction context cannot fit the configured token threshold",
        "retryable": false,
        "details": null,
    });
    runtime.apply(
        tool_result.clone(),
        failed(turn_id, error.clone())
            .observe_completed_tool_result(&tool_action, &tool_result)
            .observe(json!({
                "type": "turn_steered",
                "turn_id": turn_id,
                "content": steering_content,
            }))
            .observe(json!({
                "type": "context_compaction_failed",
                "turn_id": turn_id,
                "action_id": compaction_id,
                "compaction_id": compaction_id,
                "attempt": 1,
                "trigger": "automatic",
                "error": error.clone(),
            }))
            .observe(turn_failed(turn_id, error)),
    );
}

#[test]
fn compaction_projection_is_fitted_before_its_first_provider_call() {
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config(),
        vec![
            Message::user_text(large_text("old request")),
            assistant_text(large_text("old answer")),
        ],
    );
    let turn_id = "turn-fitted-projection";
    let request = large_text("new request");

    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    let texts = action_messages(&compaction)
        .iter()
        .filter_map(message_text)
        .collect::<Vec<_>>();
    assert!(texts.iter().all(|text| !text.contains("old request")));
    assert!(texts.iter().all(|text| !text.contains("old answer")));
    assert!(texts.iter().any(|text| text.contains("new request")));
    assert!(
        texts
            .last()
            .is_some_and(|text| text.contains("CONTEXT CHECKPOINT COMPACTION"))
    );
}

#[test]
fn compacted_replacement_fits_before_resuming_the_agent() {
    let history = (0..1_000)
        .map(|index| Message::user_text(format!("short note {index}")))
        .collect();
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(10_000),
        history,
    );
    let turn_id = "turn-fitted-replacement";
    let compaction = runtime
        .apply(
            user_message(turn_id, "continue", "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, "continue")),
        )
        .only_action();

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "preserve recent notes"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "preserve recent notes",
                )),
        )
        .only_action();

    assert_eq!(resumed["purpose"], "agent");
    assert_eq!(resumed["model_input"]["messages"]["type"], "replace");
    let preserved_notes = action_messages(&resumed)
        .iter()
        .filter_map(message_text)
        .filter(|text| text.starts_with("short note "))
        .count();
    assert!(preserved_notes > 0);
    assert!(preserved_notes < 1_000);
    assert!(
        action_messages(&resumed)
            .iter()
            .filter_map(message_text)
            .any(|text| text == "continue")
    );
}

#[test]
fn untagged_plain_text_is_accepted_as_the_compaction_summary() {
    let turn_id = "turn-plain-summary";
    let request = large_text("request requiring compaction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    let resumed = runtime
        .apply(
            text_completion(&compaction, "plain handoff summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "plain handoff summary",
                )),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn empty_summary_retries_once_then_fails_without_losing_canonical_context() {
    let turn_id = "turn-empty-summary";
    let request = large_text("request retained after empty compaction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let original_messages = action_messages(&compaction).to_vec();

    let retry = runtime
        .apply(
            reasoning_only_completion(&compaction),
            running(turn_id).dispatch(compaction_call("automatic", 0, 2)),
        )
        .only_action();

    assert_eq!(compaction["compaction_id"], retry["compaction_id"]);
    assert_ne!(action_id(&compaction), action_id(&retry));
    assert_eq!(action_messages(&retry), original_messages);

    let error = json!({
        "code": "invalid_compaction_summary",
        "message": "compaction response was empty",
        "retryable": false,
        "details": null,
    });
    runtime.apply(
        reasoning_only_completion(&retry),
        failed(turn_id, error.clone())
            .observe(context_compaction_failed(turn_id, &retry, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );

    let next_turn = runtime
        .apply(
            user_message("turn-after-empty-summary", "continue", "queue"),
            running("turn-after-empty-summary")
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started("turn-after-empty-summary", "continue")),
        )
        .only_action();
    let resumed = runtime
        .apply(
            summary_completion(&next_turn, "recovered context"),
            running("turn-after-empty-summary")
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    "turn-after-empty-summary",
                    &next_turn,
                    "recovered context",
                )),
        )
        .only_action();
    assert!(action_messages(&resumed).iter().any(|message| {
        message_text(message)
            .is_some_and(|text| text.contains("request retained after empty compaction"))
    }));
}

#[test]
fn summary_with_tool_calls_retries_instead_of_compacting() {
    let turn_id = "turn-summary-tool-call";
    let request = large_text("request requiring compaction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    let retry = runtime
        .apply(
            summary_with_tool_call_completion(&compaction, "do not accept this"),
            running(turn_id).dispatch(compaction_call("automatic", 0, 2)),
        )
        .only_action();

    assert_eq!(retry["compaction_id"], compaction["compaction_id"]);
    let resumed = runtime
        .apply(
            summary_completion(&retry, "valid summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &retry, "valid summary")),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn oversized_valid_summary_fails_the_turn_without_leaving_compaction_pending() {
    let turn_id = "turn-oversized-valid-summary";
    let request = large_text("request requiring compaction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let error = json!({
        "code": "compaction_replacement_too_large",
        "message": "compaction summary and required continuation context cannot fit the configured token threshold",
        "retryable": false,
        "details": null,
    });

    runtime.apply(
        summary_completion(&compaction, &large_text("oversized summary")),
        failed(turn_id, error.clone())
            .observe(context_compaction_failed(
                turn_id,
                &compaction,
                error.clone(),
            ))
            .observe(turn_failed(turn_id, error)),
    );
}

#[test]
fn fitted_compaction_projection_survives_checkpoint_restore() {
    let turn_id = "turn-restored-fitted-projection";
    let request = large_text("request before restore");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let projected_messages = action_messages(&compaction).to_vec();
    let checkpoint = runtime.checkpoint_value();

    runtime.restart_from_checkpoint();

    assert_eq!(runtime.checkpoint_value(), checkpoint);
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "restored summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, "restored summary")),
        )
        .only_action();
    assert_eq!(action_messages(&compaction), projected_messages);
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn steering_waits_for_preflight_compaction_then_enters_the_resumed_agent_input() {
    let turn_id = "turn-steered-preflight-compaction";
    let request = large_text("original direction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    runtime.receive_steering(turn_id, "new direction", &[&compaction]);
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "steered summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, "steered summary"))
                .observe(turn_steered(turn_id, "new direction")),
        )
        .only_action();

    assert!(
        action_messages(&resumed)
            .iter()
            .any(|message| message_text(message) == Some("new direction"))
    );
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn large_steering_during_compaction_is_preflighted_before_agent_dispatch() {
    let turn_id = "turn-large-steering-during-compaction";
    let request = large_text("original direction");
    let steering = large_text("new oversized direction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let first_compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    runtime.receive_steering(turn_id, &steering, &[&first_compaction]);
    let second_compaction = runtime
        .apply(
            summary_completion(&first_compaction, "first summary"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(context_compacted(
                    turn_id,
                    &first_compaction,
                    "first summary",
                ))
                .observe(turn_steered(turn_id, &steering)),
        )
        .only_action();

    assert_eq!(second_compaction["purpose"], "compaction");
    assert_ne!(
        second_compaction["compaction_id"],
        first_compaction["compaction_id"]
    );

    let resumed = runtime
        .apply(
            summary_completion(&second_compaction, "steering summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    turn_id,
                    &second_compaction,
                    "steering summary",
                )),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn provider_context_error_is_an_ordinary_terminal_failure() {
    let turn_id = "turn-provider-overflow";
    let mut runtime = SynchronousRuntime::new(config());
    let agent = runtime
        .apply(
            user_message(turn_id, "small request", "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "small request")),
        )
        .only_action();
    let error = json!({
        "code": "context_window_exceeded",
        "message": "provider rejected context",
        "retryable": false,
        "details": null,
    });

    runtime.apply(
        completion_failed(
            &agent,
            "context_window_exceeded",
            "provider rejected context",
        ),
        failed(turn_id, error.clone())
            .observe(failed_candidate(turn_id, &agent, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );
}

#[test]
fn automatic_compaction_failure_preserves_canonical_history() {
    let turn_id = "turn-compaction-failure";
    let request = large_text("request retained after failure");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let error = json!({
        "code": "provider_error",
        "message": "summary provider failed",
        "retryable": false,
        "details": null,
    });

    runtime.apply(
        completion_failed(&compaction, "provider_error", "summary provider failed"),
        failed(turn_id, error.clone())
            .observe(context_compaction_failed(
                turn_id,
                &compaction,
                error.clone(),
            ))
            .observe(turn_failed(turn_id, error)),
    );

    let next_turn = runtime
        .apply(
            user_message("turn-after-failure", "continue", "queue"),
            running("turn-after-failure")
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started("turn-after-failure", "continue")),
        )
        .only_action();
    let resumed = runtime
        .apply(
            summary_completion(&next_turn, "recover preserved request"),
            running("turn-after-failure")
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    "turn-after-failure",
                    &next_turn,
                    "recover preserved request",
                )),
        )
        .only_action();
    assert!(action_messages(&resumed).iter().any(|message| {
        message_text(message).is_some_and(|text| text.contains("request retained after failure"))
    }));
}

#[test]
fn manual_compaction_retries_invalid_summary_then_returns_idle() {
    let mut runtime = SynchronousRuntime::new(config());
    let compaction = runtime
        .apply(
            compact("preserve current work"),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();
    let retry = runtime
        .apply(
            reasoning_only_completion(&compaction),
            compacting("manual").dispatch(compaction_call("manual", 0, 2)),
        )
        .only_action();
    let error = json!({
        "code": "invalid_compaction_summary",
        "message": "compaction response was empty",
        "retryable": false,
        "details": null,
    });

    runtime.apply(
        reasoning_only_completion(&retry),
        idle().observe(manual_context_compaction_failed(&retry, error)),
    );
}

///
/// *Prepare*: A completed turn received keyed context from its pre-LLM hook.
/// *Do*: Compact manually, then start a turn whose pre-LLM hook returns the same keyed context.
/// *Assert*: The summary request leaves the keyed context out, and Core delivers it again after the compacted replacement.
///
#[test]
fn keyed_pre_llm_context_is_left_out_of_the_summary_and_delivered_after_compaction() {
    // Prepare
    let context = "GitHub is not connected.";
    let mut config = config();
    add_always_hook(&mut config, HookPoint::PreLlmCall);
    let binding_ids = ["test-hook-0-PreLlmCall"];
    let mut runtime = SynchronousRuntime::new(config);
    let first_hook = runtime
        .apply(
            user_message("turn-before-compaction", "inspect the repository", "queue"),
            running("turn-before-compaction")
                .dispatch(hook_call("pre_llm_call", &binding_ids, Value::Null))
                .observe(turn_started(
                    "turn-before-compaction",
                    "inspect the repository",
                )),
        )
        .only_action();
    let first_agent = runtime
        .apply(
            hook_completed(
                &first_hook,
                "pre_llm_call",
                pre_llm_continue_with_context(&[("github", context)]),
            ),
            running("turn-before-compaction").dispatch(llm_call(0)),
        )
        .only_action();
    runtime.finish_turn_with_text("turn-before-compaction", &first_agent, "first answer");

    // Do
    let compaction = runtime
        .apply(
            compact(""),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();
    runtime.apply(
        summary_completion(&compaction, "repository summary"),
        idle().observe(json!({
            "type": "context_compacted",
            "turn_id": null,
            "action_id": action_id(&compaction),
            "compaction_id": compaction["compaction_id"],
            "attempt": compaction["attempt"],
            "trigger": "manual",
            "summary": "repository summary",
            "usage": null,
        })),
    );
    let second_hook = runtime
        .apply(
            user_message("turn-after-compaction", "continue", "queue"),
            running("turn-after-compaction")
                .dispatch(hook_call("pre_llm_call", &binding_ids, Value::Null))
                .observe(turn_started("turn-after-compaction", "continue")),
        )
        .only_action();
    let resumed = runtime
        .apply(
            hook_completed(
                &second_hook,
                "pre_llm_call",
                pre_llm_continue_with_context(&[("github", context)]),
            ),
            running("turn-after-compaction").dispatch(llm_call(0)),
        )
        .only_action();

    // Assert
    assert!(
        action_messages(&compaction)
            .iter()
            .all(|message| message_text(message) != Some(context))
    );
    let delivered = action_messages(&resumed)
        .iter()
        .filter(|message| message_text(message) == Some(context))
        .count();
    assert_eq!(delivered, 1);
    assert_eq!(
        action_messages(&resumed).last().and_then(message_text),
        Some(context)
    );
    runtime.finish_turn_with_text("turn-after-compaction", &resumed, "done");
}

///
/// *Prepare*: A pre-LLM hook returns keyed context that pushes the agent input over the threshold, and compaction starts.
/// *Do*: Restart Core from its checkpoint, compact, then let the resumed pre-LLM hook return the same keyed context.
/// *Assert*: The replacement leaves room for the keyed context across the restore, so Core dispatches the agent completion instead of compacting again.
///
#[test]
fn keyed_context_redelivered_after_automatic_compaction_reaches_the_agent() {
    // Prepare
    let turn_id = "turn-keyed-context-compaction";
    let request = format!("current request {}", "0123456789abcdef".repeat(5_000));
    let context = format!("GitHub state {}", "fedcba9876543210".repeat(6_250));
    let mut config = automatic_compaction_config();
    add_always_hook(&mut config, HookPoint::PreLlmCall);
    let binding_ids = ["test-hook-0-PreLlmCall"];
    let mut runtime = SynchronousRuntime::new(config);
    let first_hook = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &binding_ids, Value::Null))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let compaction = runtime
        .apply(
            hook_completed(
                &first_hook,
                "pre_llm_call",
                pre_llm_continue_with_context(&[("github", context.as_str())]),
            ),
            running(turn_id).dispatch(compaction_call("automatic", 0, 1)),
        )
        .only_action();
    assert_eq!(
        runtime.checkpoint_value()["turn"]["phase"]["context_keys"],
        json!(["github"])
    );

    // Do
    runtime.restart_from_checkpoint();
    let second_hook = runtime
        .apply(
            summary_completion(&compaction, "repository summary"),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &binding_ids, Value::Null))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "repository summary",
                )),
        )
        .only_action();
    let resumed = runtime
        .apply(
            hook_completed(
                &second_hook,
                "pre_llm_call",
                pre_llm_continue_with_context(&[("github", context.as_str())]),
            ),
            running(turn_id).dispatch(llm_call(0)),
        )
        .only_action();

    // Assert
    assert!(
        action_messages(&compaction)
            .iter()
            .all(|message| message_text(message) != Some(context.as_str()))
    );
    let delivered = action_messages(&resumed)
        .iter()
        .filter(|message| message_text(message) == Some(context.as_str()))
        .count();
    assert_eq!(delivered, 1);
    assert_eq!(
        action_messages(&resumed).last().and_then(message_text),
        Some(context.as_str())
    );
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

///
/// *Prepare*: A pre-LLM hook is bound.
/// *Do*: The hook returns keyed context larger than the automatic compaction threshold.
/// *Assert*: The turn fails before any compaction call, because no replacement can hold the hook's current context.
///
#[test]
fn keyed_context_that_cannot_fit_fails_before_a_compaction_call() {
    // Prepare
    let mut config = automatic_compaction_config();
    add_always_hook(&mut config, HookPoint::PreLlmCall);
    let compaction_id = compaction_action_id(&config.task_id, 1);
    let mut runtime = SynchronousRuntime::new(config);

    // Do / Assert
    fail_turn_with_unfittable_context(
        &mut runtime,
        &compaction_id,
        "turn-unfittable-keyed-context",
        &large_text("GitHub state"),
    );
}

///
/// *Prepare*: A turn fails because its pre-LLM hook returned `github` context larger than the threshold.
/// *Do*: Start the next turn, and let the hook return smaller `github` context.
/// *Assert*: The hook runs before compaction, the replacement leaves room only for the smaller context, and the agent sees only that context.
///
#[test]
fn oversized_keyed_context_failure_does_not_block_the_next_turn() {
    // Prepare
    let small = "GitHub is connected.";
    let mut config = automatic_compaction_config();
    add_always_hook(&mut config, HookPoint::PreLlmCall);
    let compaction_id = compaction_action_id(&config.task_id, 1);
    let mut runtime = SynchronousRuntime::new(config);
    fail_turn_with_unfittable_context(
        &mut runtime,
        &compaction_id,
        "turn-oversized-context",
        &large_text("GitHub state"),
    );

    // Do
    let turn_id = "turn-after-oversized-context";
    let resumed = compact_turn_after_pre_llm_hook(
        &mut runtime,
        turn_id,
        pre_llm_continue_with_context(&[("github", small)]),
    );

    // Assert
    let texts = model_texts(&resumed);
    assert_eq!(texts.iter().filter(|text| **text == small).count(), 1);
    assert!(texts.iter().all(|text| !text.starts_with("GitHub state")));
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

///
/// *Prepare*: A turn fails because its pre-LLM hook returned `github` context larger than the threshold.
/// *Do*: In the next turn, the hook returns only `jira` context, or no context.
/// *Assert*: The replacement leaves no room for the stale `github` context, so compaction succeeds and the agent completion follows.
///
#[test]
fn stale_context_key_is_not_reserved() {
    for (label, output) in [
        (
            "other key",
            pre_llm_continue_with_context(&[("jira", "Jira is connected.")]),
        ),
        ("no context", json!({"type": "continue"})),
    ] {
        // Prepare
        let mut config = automatic_compaction_config();
        add_always_hook(&mut config, HookPoint::PreLlmCall);
        let compaction_id = compaction_action_id(&config.task_id, 1);
        let mut runtime = SynchronousRuntime::new(config);
        fail_turn_with_unfittable_context(
            &mut runtime,
            &compaction_id,
            "turn-stale-context",
            &large_text("GitHub state"),
        );

        // Do
        let turn_id = "turn-after-stale-context";
        let resumed = compact_turn_after_pre_llm_hook(&mut runtime, turn_id, output);

        // Assert
        assert!(
            model_texts(&resumed)
                .iter()
                .all(|text| !text.starts_with("GitHub state")),
            "{label}"
        );
        runtime.finish_turn_with_text(turn_id, &resumed, "done");
    }
}
