use super::*;
use crate::core::testing::assistant_text;
use crate::core::{CompactionPolicy, InputModality, Message};
use rstest::rstest;

fn embedded_image(mime_type: &str, blob: &str) -> Value {
    json!({
        "type": "resource",
        "resource": {
            "uri": "resource://picture",
            "mimeType": mime_type,
            "blob": blob,
        },
    })
}

fn user_content(turn_id: &str, content: &Value) -> Value {
    json!({"type": "user_message", "turn_id": turn_id, "mode": "queue", "content": content})
}

fn started_content(turn_id: &str, content: &Value) -> Value {
    json!({"type": "turn_started", "turn_id": turn_id, "content": content})
}

fn model_blocks(action: &Value) -> Vec<&Value> {
    action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("completion has model messages")
        .iter()
        .flat_map(|message| message["content"].as_array().into_iter().flatten())
        .collect()
}

/// *Prepare*: Fixed image cost and a budget smaller than a large embedded blob.
/// *Do*: Queue the image with a question.
/// *Assert*: Both small and large images reach the agent without compaction;
/// canonical conversation retains the embedded resource.
#[rstest]
#[case("image/png", 12)]
#[case("image/png", 200_000)]
#[case("IMAGE/JPEG", 200_000)]
#[case("image/gif", 200_000)]
#[case("image/webp", 200_000)]
fn fixed_cost_embedded_images_reach_the_agent(#[case] mime_type: &str, #[case] size: usize) {
    // Prepare
    let turn_id = "embedded-image";
    let mut config = config();
    config.settings.context.compaction = CompactionPolicy::Automatic;
    config.settings.context.model_facts.compaction_threshold = 40_000;
    let mut runtime = SynchronousRuntime::new(config);
    let blob = "a".repeat(size);
    let content =
        json!([embedded_image(mime_type, &blob), {"type": "text", "text": "describe it"}]);

    // Do
    let action = runtime
        .apply(
            user_content(turn_id, &content),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(started_content(turn_id, &content)),
        )
        .only_action();

    // Assert
    assert!(model_blocks(&action).contains(&&json!({
        "type": "image", "data": blob, "mimeType": mime_type.to_ascii_lowercase(),
    })));
    runtime.finish_turn_with_text(turn_id, &action, "a picture");
    assert_eq!(
        runtime.export_conversation(json!({"mode": "forked", "turns": "all"}))["messages"][0]["message"]
            ["content"],
        content
    );
}

/// *Prepare*: An embedded image with annotations and opaque resource metadata.
/// *Do*: Queue it for a vision model.
/// *Assert*: Native model input preserves metadata, with outer metadata taking
/// precedence over nested metadata on a shared key.
#[test]
fn embedded_image_projection_preserves_metadata() {
    // Prepare
    let turn_id = "embedded-image-metadata";
    let mut image = embedded_image("image/png", "aGVsbG8=");
    image["annotations"] = json!({"priority": 0.5});
    image["_meta"] = json!({"outer": true, "shared": "outer"});
    image["resource"]["_meta"] = json!({"nested": true, "shared": "nested"});
    let content = json!([image]);
    let mut runtime = SynchronousRuntime::new(config());

    // Do
    let action = runtime
        .apply(
            user_content(turn_id, &content),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(started_content(turn_id, &content)),
        )
        .only_action();

    // Assert
    let image = model_blocks(&action)
        .into_iter()
        .find(|block| block["type"] == "image")
        .expect("embedded image is projected natively");
    assert_eq!(image["annotations"], content[0]["annotations"]);
    assert_eq!(
        image["_meta"],
        json!({"outer": true, "nested": true, "shared": "outer"})
    );
}

/// *Prepare*: A text-only model and embedded text, an image, and other blobs.
/// *Do*: Queue them together.
/// *Assert*: Only image blobs become placeholders; other resources
/// retain their existing representations.
#[test]
fn text_only_projection_replaces_only_embedded_images() {
    // Prepare
    let turn_id = "text-only-embedded-image";
    let mut config = config();
    config.settings.context.model_facts.input_modalities = vec![InputModality::Text];
    let mut runtime = SynchronousRuntime::new(config);
    let content = json!([
        embedded_image("image/png", "aGVsbG8="),
        {"type": "resource", "resource": {"uri": "resource://text", "mimeType": "image/png", "text": "notes"}},
        embedded_image("application/pdf", "aGVsbG8="),
        embedded_image("image/svg+xml", "aGVsbG8="),
        {"type": "resource", "resource": {"uri": "resource://unknown", "blob": "aGVsbG8="}},
    ]);

    // Do
    let action = runtime
        .apply(
            user_content(turn_id, &content),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(started_content(turn_id, &content)),
        )
        .only_action();

    // Assert
    let blocks = model_blocks(&action);
    assert_eq!(
        blocks[1],
        &json!({"type": "text", "text": "[Image] [resource: resource://picture]"})
    );
    assert_eq!(blocks[2], &content[1]);
    assert_eq!(blocks[3], &content[2]);
    assert_eq!(blocks[4], blocks[1]);
    assert_eq!(blocks[5], &content[4]);
}

/// *Prepare*: A large embedded image is the latest user request, and old text
/// makes automatic compaction necessary under the fixed image cost.
/// *Do*: Start compaction, restore the checkpoint, then accept a short summary.
/// *Assert*: The summary covers earlier history, while the resumed agent and
/// canonical conversation retain the latest image.
#[test]
fn embedded_image_survives_compaction_and_restore() {
    // Prepare
    let turn_id = "compact-embedded-image";
    let mut config = config();
    config.settings.context.compaction = CompactionPolicy::Automatic;
    config.settings.context.model_facts.compaction_threshold = 40_000;
    let history = vec![
        Message::user_text("old text ".repeat(25_000)),
        assistant_text("old answer"),
    ];
    let mut runtime = SynchronousRuntime::new_with_history(config, history);
    let content = json!([embedded_image("image/png", &"a".repeat(200_000))]);

    // Do
    let compaction = runtime
        .apply(
            user_content(turn_id, &content),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(started_content(turn_id, &content)),
        )
        .only_action();
    assert!(
        !model_blocks(&compaction)
            .iter()
            .any(|block| { matches!(block["type"].as_str(), Some("image" | "resource")) })
    );
    assert!(!compaction.to_string().contains(&"a".repeat(200_000)));
    runtime.restart_from_checkpoint();
    let resumed = runtime
        .apply(
            text_completion(&compaction, "<summary>Describe the picture.</summary>"),
            running(turn_id).dispatch(llm_call(0)).observe(json!({
                "type": "context_compacted", "turn_id": turn_id,
                "action_id": action_id(&compaction),
                "compaction_id": compaction["compaction_id"],
                "attempt": compaction["attempt"], "trigger": "automatic",
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0},
                "summary": "Describe the picture.",
            })),
        )
        .only_action();

    // Assert
    assert!(
        model_blocks(&resumed)
            .iter()
            .any(|block| block["type"] == "image")
    );
    runtime.finish_turn_with_text(turn_id, &resumed, "a picture");
    let conversation = runtime.export_conversation(json!({"mode": "forked", "turns": "all"}));
    assert!(
        conversation["messages"]
            .as_array()
            .expect("conversation has messages")
            .iter()
            .any(|message| message["message"]["content"] == content)
    );
}

/// *Prepare*: Embedded image history and a text-only model.
/// *Do*: Request manual compaction.
/// *Assert*: The summarizer receives the image placeholder, never the blob.
#[test]
fn text_only_compaction_projects_embedded_images() {
    // Prepare
    let mut config = config();
    config.settings.context.model_facts.input_modalities = vec![InputModality::Text];
    let history = vec![
        serde_json::from_value(json!({
            "role": "user", "content": [embedded_image("image/png", "aGVsbG8=")],
        }))
        .expect("embedded image history is valid"),
        assistant_text("a picture"),
    ];
    let mut runtime = SynchronousRuntime::new_with_history(config, history);

    // Do
    let action = runtime
        .apply(
            json!({"type": "compact", "extra_instructions": ""}),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();

    // Assert
    assert!(
        model_blocks(&action)
            .contains(&&json!({"type": "text", "text": "[Image] [resource: resource://picture]"}))
    );
    assert!(
        !model_blocks(&action)
            .iter()
            .any(|block| matches!(block["type"].as_str(), Some("image" | "resource")))
    );
}
