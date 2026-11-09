//! How images count toward the budgets and come through a compaction.

use super::*;

///
/// *Prepare*: A text-only model, automatic compaction at 40,000 tokens.
/// *Do*: Queue a turn with a file image whose base64 data alone passes the threshold.
/// *Assert*: Core sends the agent request without compacting: the model gets the
/// placeholder with the file's uri, and the budget counts that text.
///
#[test]
fn a_text_only_model_budgets_an_embedded_image_as_its_placeholder() {
    let turn_id = "turn-file-link-image-budget";
    let content = file_image_content();
    let mut runtime = SynchronousRuntime::new(text_only(automatic_compaction_config()));

    let action = runtime
        .apply(
            user_message_with_content(turn_id, content.clone()),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(turn_id, content)),
        )
        .only_action();

    assert_eq!(action["purpose"], "agent");
    assert!(action_text_blocks(&action).contains(&FILE_IMAGE_PLACEHOLDER));
}

#[test]
fn compaction_uses_the_placeholder_then_resumes_the_agent_with_the_image() {
    let turn_id = "turn-compaction-file-link-budget";
    let config = with_text_only_compaction_model(automatic_compaction_config_with_image_cost(
        80_000,
        LARGE_IMAGE_TOKENS,
    ));
    // The image is part of the history the summary covers.
    let image_message =
        Message::user(serde_json::from_value(file_image_content()).expect("image content"));
    let mut runtime = SynchronousRuntime::new_with_history(
        config,
        vec![
            Message::user_text(large_text("old request")),
            assistant_text("old answer"),
            image_message,
            assistant_text("seen the screenshot"),
        ],
    );

    let action = runtime
        .apply(
            user_message(turn_id, "next", "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, "next")),
        )
        .only_action();

    assert_eq!(action["purpose"], "compaction");
    assert!(action_text_blocks(&action).contains(&FILE_IMAGE_PLACEHOLDER));

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
}

#[test]
fn impossible_native_replacement_fails_before_placeholder_compaction_call() {
    let turn_id = "turn-unfittable-native-image-replacement";
    let config = with_text_only_compaction_model(automatic_compaction_config_with_image_cost(
        40_000,
        LARGE_IMAGE_TOKENS,
    ));
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

/// A steered image of about 50,000 tokens by serialized size arrives after the
/// agent's last reply. It cannot go back after the summary within the
/// threshold, so the rebuilt history shows the exchange and the image message
/// as a clipped reduced view, with the image as `[Image]`, while the tool
/// result stays committed.
#[test]
fn a_steered_image_over_the_threshold_comes_back_as_a_reduced_view() {
    let turn_id = "turn-unfittable-compaction";
    let config = automatic_compaction_config_with_image_cost(40_000, LARGE_IMAGE_TOKENS);
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
    let compaction = runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(compaction_call("automatic", 1, 1))
                .observe_completed_tool_result(&tool_action, &tool_result)
                .observe(json!({
                    "type": "turn_steered",
                    "turn_id": turn_id,
                    "content": steering_content,
                })),
        )
        .only_action();

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "the user sent a screenshot"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "the user sent a screenshot",
                ))
                .observe(inputs_reduced(turn_id, 3, "clipped")),
        )
        .only_action();
    let view = action_messages(&resumed)
        .iter()
        .filter_map(message_text)
        .find(|text| text.starts_with(REDUCED_VIEW_HEADER))
        .expect("the rebuilt history has the reduced view");
    assert_eq!(
        view,
        format!(
            "{REDUCED_VIEW_HEADER}\nread_file(path=\"small.txt\")\n<OK> {{\"path\":\"small.txt\",\
             \"content\":\"small file contents[CONTENT OMITTED FOR SIZE: 41 MORE CHARACTERS]9,\
             \"offset\":0,\"lines_read\":1,\"was_truncated\":false}}\nUserMessage: [Image]"
        )
    );
}

/// *Prepare*: Automatic compaction is enabled with a 40,000-token threshold and
/// the fixed image token cost, so one native image estimates at the constant
/// instead of its serialized size.
/// *Do*: Send one prompt carrying a large image saved to a file (an embedded
/// resource whose base64 data alone passes the threshold).
/// *Assert*: The turn reaches the agent completion with the image delivered
/// as an image, because the fixed cost keeps the input under the threshold. This
/// turn does not compact; the image-in-scope scenarios cover fitting a
/// compaction request that carries an image.
#[test]
fn fixed_image_cost_lets_one_prompt_and_one_image_reach_the_agent() {
    let turn_id = "turn-fixed-image-cost-single-image";
    let config = automatic_compaction_config_with_threshold(40_000);
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
    let image = first_action_block_with_type(&action, "image");
    assert_eq!(image["mimeType"], "image/png");
    assert!(image["data"].as_str().is_some_and(|data| !data.is_empty()));
}

/// The threshold of the image scenarios: one native image at the fixed cost
/// fits with room for the reply and a small tool result.
const IMAGE_SCENARIO_THRESHOLD: u64 = 16_000;

/// An image cost above the test facts' summary request budget
/// (`compaction_threshold_max`), so no request carrying the image natively fits.
const RESTORED_IMAGE_TOKENS: u64 = 250_000;

/// Leaves an automatic compaction pending whose kept context holds a native
/// image, and returns its Action. An older exchange of about 4,000 tokens
/// comes first. The turn's request carries the image, the agent calls a tool,
/// and its result of about 3,000 tokens crosses the threshold. The image is in
/// the in-context summary request, and the rebuilt context carries it with
/// the request.
fn pending_compaction_with_image_in_scope(
    config: HarnessConfig,
    turn_id: &str,
) -> (SynchronousRuntime, Value) {
    let mut runtime = SynchronousRuntime::new_with_history(
        config,
        vec![
            Message::user_text("hello"),
            assistant_text(format!("old answer {}", "0123456789abcdef".repeat(1_000))),
        ],
    );
    let content = json!([
        {"type": "text", "text": "read the file next to this screenshot"},
        {"type": "image", "data": "a".repeat(200_000), "mimeType": "image/png"},
    ]);
    let first_agent = runtime
        .apply(
            user_message_with_content(turn_id, content.clone()),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(turn_id, content)),
        )
        .only_action();
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

    let tool_output = format!("file contents {}", "0123456789abcdef".repeat(750));
    let tool_result = file_read_success(&tool_action, &tool_output);
    let expected = running(turn_id)
        .dispatch(compaction_call("automatic", 1, 1))
        .observe_completed_tool_result(&tool_action, &tool_result);
    let compaction = runtime.apply(tool_result, expected).only_action();
    assert!(compaction.get("request").is_none(), "{compaction}");
    (runtime, compaction)
}

fn has_image(action: &Value) -> bool {
    action_messages(action)
        .iter()
        .flat_map(|message| message["content"].as_array().into_iter().flatten())
        .any(|block| block["type"] == "image")
}

/// *Prepare*: Automatic compaction with the fixed image token cost. A turn
/// whose request carries a large native image calls a tool, and its result
/// crosses the threshold.
/// *Do*: Complete the in-context summary request.
/// *Assert*: The summary request carries the image natively, because the
/// fixed cost keeps it inside the threshold, and the resumed agent input
/// carries it too.
#[test]
fn fixed_image_cost_lets_a_native_image_stay_through_compaction() {
    let turn_id = "turn-fixed-image-cost-compaction";
    let config = automatic_compaction_config_with_threshold(IMAGE_SCENARIO_THRESHOLD);
    let (mut runtime, compaction) = pending_compaction_with_image_in_scope(config, turn_id);
    assert!(has_image(&compaction));

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "the user sent a screenshot"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "the user sent a screenshot",
                )),
        )
        .only_action();
    assert_eq!(resumed["purpose"], "agent");
    assert!(has_image(&resumed));
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// *Prepare*: The same in-flight compaction, checkpointed under the fixed
/// image token cost.
/// *Do*: Restore the checkpoint with the fixed policy, then complete the summary.
/// *Assert*: The replacement is budgeted with the fixed cost, so the agent
/// resumes with the native image.
#[test]
fn fixed_image_cost_budgets_an_in_flight_compaction_after_restore() {
    let turn_id = "turn-fixed-image-cost-restore";
    let config = automatic_compaction_config_with_threshold(IMAGE_SCENARIO_THRESHOLD);
    let (mut runtime, compaction) = pending_compaction_with_image_in_scope(config, turn_id);

    runtime.restart_from_checkpoint();

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "restored with the fixed cost"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "restored with the fixed cost",
                )),
        )
        .only_action();
    assert!(has_image(&resumed));
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// *Prepare*: The same in-flight compaction, checkpointed under the fixed cost.
/// *Do*: Restore the checkpoint with an image cost rule above the summary
/// request budget.
/// *Assert*: The checkpoint does not store the rule, so the restoring
/// configuration budgets the image with its own rule. The in-context request
/// no longer fits, and the compaction goes on as a standalone request.
#[test]
fn restore_budgets_an_in_flight_compaction_under_the_restoring_image_policy() {
    let turn_id = "turn-serialized-image-cost-restore";
    let config = automatic_compaction_config_with_threshold(IMAGE_SCENARIO_THRESHOLD);
    let (mut runtime, _) = pending_compaction_with_image_in_scope(config, turn_id);

    runtime.restart_from_checkpoint_with_config(automatic_compaction_config_with_image_cost(
        IMAGE_SCENARIO_THRESHOLD,
        RESTORED_IMAGE_TOKENS,
    ));

    assert_eq!(
        runtime.checkpoint_value()["turn"]["phase"]["projection"]["phase"],
        "standalone"
    );
}
