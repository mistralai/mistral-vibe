use super::*;
use crate::core::testing::{assistant_text, compaction_action_id};
use crate::core::{CompactionPolicy, InputModality, Message};

fn text_only_agent_config() -> HarnessConfig {
    let mut config = config();
    config.settings.context.input_modalities = vec![InputModality::Text];
    config
}

fn inline_image() -> Value {
    json!({"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"})
}

fn file_backed_image() -> Value {
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
        blocks.contains(&&json!({"type": "text", "text": "[image]"})),
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
/// *Do*: Queue a turn with a file-backed image.
/// *Assert*: The agent completion carries the file link instead of the image.
///
#[test]
fn text_only_agent_receives_a_file_backed_image_as_its_file_link() {
    // Prepare
    let turn_id = "turn-text-only-file-link";
    let mut runtime = SynchronousRuntime::new(text_only_agent_config());
    let content = json!([file_backed_image()]);

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
    assert_eq!(content_types(&action), ["text", "resource_link"]);
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
    config.settings.context.compaction = CompactionPolicy::Automatic {
        token_threshold: 40_000,
    };
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
    config.settings.context.input_modalities = vec![InputModality::Text];
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
