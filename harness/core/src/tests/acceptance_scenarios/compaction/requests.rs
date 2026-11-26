//! What the agent requests carry, and which changes rebuild the cached messages.

use super::*;

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
fn switching_to_an_image_model_replaces_the_runtime_message_cache() {
    let first_turn_id = "turn-file-link-before-model-switch";
    let mut config = text_only(config());
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
    assert!(action_text_blocks(&first_action).contains(&FILE_IMAGE_PLACEHOLDER));
    runtime.finish_turn_with_text(first_turn_id, &first_action, "done");

    config.settings.context.model_facts.input_modalities =
        vec![InputModality::Text, InputModality::Image];
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
    let checkpoint = runtime.checkpoint_value();
    let stored = checkpoint["context"]["messages"]
        .as_array()
        .expect("checkpoint has model messages")
        .iter()
        .flat_map(|stored| {
            stored["message"]["content"]
                .as_array()
                .into_iter()
                .flatten()
        })
        .find(|block| block["type"] == "resource")
        .expect("checkpoint keeps the embedded image as the app wrote it");
    assert_eq!(
        stored["resource"]["uri"],
        "file:///workspace/screenshot.png"
    );
}

#[test]
fn keeps_agent_message_cache_when_only_the_compaction_model_changes() {
    let first_turn_id = "turn-before-compaction-model-switch";
    let config = config();
    let mut runtime = SynchronousRuntime::new(config.clone());
    let first_action = runtime.start_turn(first_turn_id, "first request");
    runtime.finish_turn_with_text(first_turn_id, &first_action, "done");

    let config = with_text_only_compaction_model(config);
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
