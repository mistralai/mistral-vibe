use super::*;
use crate::core::testing::{assistant_text, compaction_action_id};
use crate::core::{CompactionPolicy, InputModality, Message};

fn text_only_agent_config() -> HarnessConfig {
    let mut config = config();
    config.settings.context.model_facts.input_modalities = vec![InputModality::Text];
    config
}

fn inline_image() -> Value {
    json!({"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"})
}

/// An image with the file link an older Vibe stored in `_meta`. Core ignores
/// that key: such an image is an image like any other.
fn image_with_legacy_vibe_file_link() -> Value {
    json!({
        "type": "image",
        "data": "aGVsbG8=",
        "mimeType": "image/png",
        "_meta": {
            "mistralai.vibe.harness/file-image-resource-link": {
                "type": "resource_link",
                "uri": "file:///workspace/screenshot.png",
                "name": "screenshot.png",
                "mimeType": "image/png",
            },
        },
    })
}

/// An image the app saved to a file: a standard embedded resource whose uri
/// is the file.
fn embedded_file_image() -> Value {
    json!({
        "type": "resource",
        "resource": {"uri": "file:///workspace/shot.png", "mimeType": "image/png", "blob": "aGVsbG8="},
    })
}

/// What a model without image input receives for `embedded_file_image()`.
fn embedded_file_image_placeholder() -> Value {
    json!({"type": "text", "text": "[Image] [resource: file:///workspace/shot.png]"})
}

fn web_image_link() -> Value {
    json!({
        "type": "resource_link",
        "uri": "https://example.test/chart.png",
        "name": "chart.png",
        "mimeType": "image/png",
    })
}

fn user_message_with_content(turn_id: &str, content: &Value) -> Value {
    json!({
        "type": "user_message",
        "turn_id": turn_id,
        "mode": "queue",
        "content": content,
    })
}

fn turn_started_with_content(turn_id: &str, content: &Value) -> Value {
    json!({"type": "turn_started", "turn_id": turn_id, "content": content})
}

fn user_image_history() -> Vec<Message> {
    vec![
        serde_json::from_value(json!({
            "role": "user",
            "content": [inline_image(), {"type": "text", "text": "remember this"}],
        }))
        .expect("user image message is valid"),
        assistant_text("noted"),
    ]
}

fn model_blocks(action: &Value) -> Vec<&Value> {
    action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("completion action has model messages")
        .iter()
        .flat_map(|message| message["content"].as_array().into_iter().flatten())
        .collect()
}

fn content_types(action: &Value) -> Vec<&str> {
    model_blocks(action)
        .into_iter()
        .filter_map(|block| block["type"].as_str())
        .collect()
}

fn assert_image_placeholder(action: &Value) {
    let blocks = model_blocks(action);
    assert!(
        blocks.iter().all(|block| block["type"] != "image"),
        "a text-only model receives no image block"
    );
    assert!(
        blocks.contains(&&json!({"type": "text", "text": "[Image]"})),
        "the image reaches the model as the text placeholder"
    );
}

///
/// *Prepare*: The settings accept only text.
/// *Do*: Queue a turn whose user message contains an image.
/// *Assert*: The agent completion carries the text placeholder in place of the image.
///
#[test]
fn text_only_agent_receives_a_user_image_as_a_placeholder() {
    // Prepare
    let turn_id = "turn-text-only-user-image";
    let mut runtime = SynchronousRuntime::new(text_only_agent_config());
    let content = json!([inline_image(), {"type": "text", "text": "inspect this"}]);

    // Do
    let action = runtime
        .apply(
            user_message_with_content(turn_id, &content),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(turn_id, &content)),
        )
        .only_action();

    // Assert
    assert_eq!(content_types(&action), ["text", "text", "text"]);
    assert_image_placeholder(&action);
}

///
/// *Prepare*: The settings accept text and images.
/// *Do*: Queue a turn whose user message contains an image.
/// *Assert*: The agent completion carries the image.
///
#[test]
fn vision_agent_receives_the_user_image() {
    // Prepare
    let turn_id = "turn-vision-user-image";
    let mut runtime = SynchronousRuntime::new(config());
    let content = json!([inline_image(), {"type": "text", "text": "inspect this"}]);

    // Do
    let action = runtime
        .apply(
            user_message_with_content(turn_id, &content),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(turn_id, &content)),
        )
        .only_action();

    // Assert
    assert_eq!(content_types(&action), ["text", "image", "text"]);
}

///
/// *Prepare*: The settings accept only text.
/// *Do*: Queue a turn whose user image carries an older Vibe's file link in `_meta`.
/// *Assert*: The agent completion carries the text placeholder, not the file link.
///
#[test]
fn text_only_agent_ignores_a_legacy_vibe_file_link_on_a_user_image() {
    // Prepare
    let turn_id = "turn-text-only-user-file-image";
    let mut runtime = SynchronousRuntime::new(text_only_agent_config());
    let content = json!([image_with_legacy_vibe_file_link()]);

    // Do
    let action = runtime
        .apply(
            user_message_with_content(turn_id, &content),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(turn_id, &content)),
        )
        .only_action();

    // Assert
    assert_eq!(content_types(&action), ["text", "text"]);
    assert_image_placeholder(&action);
}

///
/// *Prepare*: The settings accept only text.
/// *Do*: Queue a turn whose user message embeds an image resource and links a file.
/// *Assert*: The embedded image becomes the placeholder with its uri, and the link stays a link.
///
#[test]
fn text_only_agent_receives_an_embedded_image_as_a_placeholder_with_its_link() {
    // Prepare
    let turn_id = "turn-text-only-embedded-image";
    let mut runtime = SynchronousRuntime::new(text_only_agent_config());
    let link = json!({
        "type": "resource_link",
        "uri": "file:///workspace/notes.png",
        "name": "notes.png",
        "mimeType": "image/png",
    });
    let content = json!([embedded_file_image(), link]);

    // Do
    let action = runtime
        .apply(
            user_message_with_content(turn_id, &content),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(turn_id, &content)),
        )
        .only_action();

    // Assert
    assert_eq!(content_types(&action), ["text", "text", "resource_link"]);
    let blocks = model_blocks(&action);
    assert!(blocks.contains(&&embedded_file_image_placeholder()));
    assert!(blocks.contains(&&link));
}

///
/// *Prepare*: The settings accept text and images.
/// *Do*: Queue a turn whose user message embeds an image resource.
/// *Assert*: The agent completion carries it as an image block with the embedded data.
///
#[test]
fn vision_agent_receives_an_embedded_image_as_an_image() {
    // Prepare
    let turn_id = "turn-vision-embedded-image";
    let mut runtime = SynchronousRuntime::new(config());
    let content = json!([embedded_file_image()]);

    // Do
    let action = runtime
        .apply(
            user_message_with_content(turn_id, &content),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(turn_id, &content)),
        )
        .only_action();

    // Assert
    assert!(
        model_blocks(&action)
            .contains(&&json!({"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"}))
    );
    assert!(!content_types(&action).contains(&"resource"));
}

///
/// *Prepare*: One agent accepts only text, one accepts images.
/// *Do*: Queue a turn whose user message links a web image.
/// *Assert*: Both agents receive the link unchanged.
///
#[test]
fn a_web_image_link_stays_a_link_for_every_model() {
    for agent_config in [text_only_agent_config(), config()] {
        // Prepare
        let turn_id = "turn-web-image-link";
        let mut runtime = SynchronousRuntime::new(agent_config);
        let content = json!([web_image_link()]);

        // Do
        let action = runtime
            .apply(
                user_message_with_content(turn_id, &content),
                running(turn_id)
                    .dispatch(llm_call(0))
                    .observe(turn_started_with_content(turn_id, &content)),
            )
            .only_action();

        // Assert
        assert!(model_blocks(&action).contains(&&web_image_link()));
    }
}

///
/// *Prepare*: The agent accepts images; the compaction model accepts only text.
/// *Do*: Request manual compaction over history with an image.
/// *Assert*: The compaction request carries the placeholder, decided by the compaction model.
///
#[test]
fn a_text_only_compaction_model_receives_an_image_as_a_placeholder() {
    // Prepare
    let mut config = config();
    config.settings.context.compaction_model_input_modalities = Some(vec![InputModality::Text]);
    let mut runtime = SynchronousRuntime::new_with_history(config, user_image_history());

    // Do
    let compaction = runtime
        .apply(
            json!({"type": "compact", "extra_instructions": ""}),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();

    // Assert
    assert_image_placeholder(&compaction);
}

fn tool_image_history(image: Value) -> Vec<Message> {
    let call_id = "call-screenshot";
    [
        json!({"role": "user", "content": [{"type": "text", "text": "take a screenshot"}]}),
        model_assistant_tool_calls(&[tool_call(call_id, "screenshot", json!({}))]),
        json!({
            "role": "tool",
            "tool_call_id": call_id,
            "name": "screenshot",
            "outcome": "success",
            "content": [image],
        }),
        json!({"role": "assistant", "content": [{"type": "text", "text": "taken"}]}),
    ]
    .into_iter()
    .map(|message| serde_json::from_value(message).expect("history message is valid"))
    .collect()
}

fn tool_result_blocks(action: &Value) -> Vec<&Value> {
    action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("completion action has model messages")
        .iter()
        .filter(|message| message["role"] == "tool")
        .flat_map(|message| message["content"].as_array().into_iter().flatten())
        .collect()
}

fn completion_after_tool_image(image: Value) -> Value {
    let turn_id = "turn-after-tool-image";
    let mut runtime =
        SynchronousRuntime::new_with_history(text_only_agent_config(), tool_image_history(image));
    runtime
        .apply(
            user_message(turn_id, "what did it show?", "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "what did it show?")),
        )
        .only_action()
}

///
/// *Prepare*: History has a tool result with an image saved to a file (an embedded resource),
/// and the settings accept only text.
/// *Do*: Queue a turn.
/// *Assert*: The tool result reaches the model as the placeholder with the file's uri.
///
#[test]
fn text_only_agent_receives_an_embedded_tool_image_as_a_placeholder_with_its_link() {
    // Do
    let action = completion_after_tool_image(embedded_file_image());

    // Assert
    assert_eq!(
        tool_result_blocks(&action),
        [&embedded_file_image_placeholder()]
    );
}

///
/// *Prepare*: History has a tool image with an older Vibe's file link in `_meta`, and the
/// settings accept only text.
/// *Do*: Queue a turn.
/// *Assert*: The tool result reaches the model as the bare placeholder: Core ignores the key.
///
#[test]
fn text_only_agent_ignores_a_legacy_vibe_file_link_on_a_tool_image() {
    // Do
    let action = completion_after_tool_image(image_with_legacy_vibe_file_link());

    // Assert
    assert_eq!(
        tool_result_blocks(&action),
        [&json!({"type": "text", "text": "[Image]"})]
    );
}

///
/// *Prepare*: History has a tool result with an image and no file, and the settings accept only text.
/// *Do*: Queue a turn.
/// *Assert*: The tool result reaches the model as the text placeholder.
///
#[test]
fn text_only_agent_receives_a_tool_image_without_a_file_as_a_placeholder() {
    // Do
    let action = completion_after_tool_image(inline_image());

    // Assert
    assert_eq!(
        tool_result_blocks(&action),
        [&json!({"type": "text", "text": "[Image]"})]
    );
}

///
/// *Prepare*: Automatic compaction is due, history has an image, and the settings accept only text.
/// *Do*: Queue a turn.
/// *Assert*: Core requests the compaction with the text placeholder in place of the image.
///
#[test]
fn text_only_automatic_compaction_receives_an_image_as_a_placeholder() {
    // Prepare
    let turn_id = "turn-text-only-compaction";
    let mut config = text_only_agent_config();
    config.settings.context.compaction = CompactionPolicy::Automatic;
    config.settings.context.model_facts.compaction_threshold = 40_000;
    let mut history = vec![
        Message::user_text(format!("old request {}", "0123456789abcdef".repeat(12_000))),
        assistant_text("old answer"),
    ];
    history.extend(user_image_history());
    let mut runtime = SynchronousRuntime::new_with_history(config, history);

    // Do
    let compaction = runtime
        .apply(
            user_message(turn_id, "continue", "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, "continue")),
        )
        .only_action();

    // Assert
    assert_image_placeholder(&compaction);
}

///
/// *Prepare*: History has an image, and the settings accept only text.
/// *Do*: Request manual compaction.
/// *Assert*: Core requests the compaction with the text placeholder in place of the image.
///
#[test]
fn text_only_manual_compaction_receives_an_image_as_a_placeholder() {
    // Prepare
    let config = text_only_agent_config();
    let compaction_id = compaction_action_id(&config.task_id, 1);
    let mut runtime = SynchronousRuntime::new_with_history(config, user_image_history());

    // Do
    let compaction = runtime
        .apply(
            json!({"type": "compact", "extra_instructions": ""}),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();

    // Assert
    assert_eq!(compaction["action_id"], compaction_id);
    assert_image_placeholder(&compaction);
}

///
/// *Prepare*: A vision agent finishes a turn that contains an image.
/// *Do*: Reconfigure the settings to accept only text, then queue a text turn.
/// *Assert*: Core replaces the Runtime's model messages, and the earlier image becomes the placeholder.
///
#[test]
fn reconfiguring_to_a_text_only_model_sends_an_earlier_image_as_a_placeholder() {
    // Prepare
    let first_turn_id = "turn-image-before-switch";
    let mut config = config();
    let mut runtime = SynchronousRuntime::new(config.clone());
    let content = json!([inline_image(), {"type": "text", "text": "inspect this"}]);
    let action = runtime
        .apply(
            user_message_with_content(first_turn_id, &content),
            running(first_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started_with_content(first_turn_id, &content)),
        )
        .only_action();
    runtime.finish_turn_with_text(first_turn_id, &action, "a screenshot");
    config.settings.context.model_facts.input_modalities = vec![InputModality::Text];
    runtime.reconfigure_settings(
        config.settings,
        completed(
            first_turn_id,
            vec![json!({"type": "text", "text": "a screenshot"})],
        ),
    );
    let second_turn_id = "turn-text-after-switch";

    // Do
    let action = runtime
        .apply(
            user_message(second_turn_id, "describe it again", "queue"),
            running(second_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(second_turn_id, "describe it again")),
        )
        .only_action();

    // Assert
    assert_eq!(action["model_input"]["messages"]["type"], "replace");
    assert_image_placeholder(&action);
}
