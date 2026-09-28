use super::*;
use crate::core::{BackgroundProcessMode, CompactionPolicy};
use uuid::Uuid;

fn compact(extra_instructions: &str) -> Value {
    json!({
        "type": "compact",
        "extra_instructions": extra_instructions,
    })
}

fn completion_failed(action_id: &str, message: &str) -> Value {
    json!({
        "type": "completion_failed",
        "action_id": action_id,
        "error": {
            "code": "provider_error",
            "message": message,
            "retryable": false,
            "details": null,
        },
    })
}

fn summary_completion(action_id: &str, summary: &str) -> Value {
    completion(
        action_id,
        json!([{"type": "text", "text": format!("<summary>{summary}</summary>")}]),
    )
}

fn reconfigure(changes: Value) -> Value {
    json!({
        "type": "reconfigure",
        "changes": changes,
    })
}

///
/// *Prepare*: Automatic compaction is configured with a positive threshold below Core's fixed request overhead.
/// *Do*: Create a session through the public Core interface.
/// *Assert*: Creation fails before any Runtime Action can be emitted.
///
#[test]
fn automatic_compaction_rejects_a_budget_below_fixed_model_input() {
    // Prepare
    let mut config = config();
    config.settings.context.compaction = CompactionPolicy::Automatic { token_threshold: 1 };

    // Do
    let error = HarnessSession::create(config)
        .err()
        .expect("an impossible model-input budget rejects session creation");

    // Assert
    assert_eq!(
        error.to_string(),
        "token threshold cannot fit the generated system prompt, tool catalog, and compaction prompt"
    );
}

fn dispatched_action(result: &Value) -> &Value {
    &result["transition"]["next"]["directives"][0]["action"]
}

fn model_messages(action: &Value) -> &[Value] {
    action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("completion action contains model messages")
}

fn serialized_messages(action: &Value) -> String {
    serde_json::to_string(model_messages(action)).expect("model messages serialize")
}

fn message_text(message: &Value) -> &str {
    message["content"][0]["text"]
        .as_str()
        .expect("message begins with text content")
}

///
/// *Prepare*: Two identical sessions request manual compaction and then retry after the same failure.
/// *Do*: Read each compaction Action ID from the serialized Step Protocol transitions.
/// *Assert*: Core emits stable opaque UUIDs, and each compaction attempt receives a distinct ID.
///
#[test]
fn compaction_action_ids_are_deterministic_opaque_uuids() {
    // Prepare
    let mut first = HarnessSession::create(config()).expect("first session creates");
    let mut second = HarnessSession::create(config()).expect("second session creates");

    // Do
    let first_requested = accepted_value(first.apply(input(1, compact("first attempt"))));
    let second_requested = accepted_value(second.apply(input(1, compact("first attempt"))));
    let first_action_id = dispatched_action_id(&first_requested).to_string();
    let second_action_id = dispatched_action_id(&second_requested).to_string();

    accepted_value(first.apply(input(
        2,
        completion_failed(&first_action_id, "summary failed"),
    )));
    accepted_value(second.apply(input(
        2,
        completion_failed(&second_action_id, "summary failed"),
    )));

    let first_retried = accepted_value(first.apply(input(3, compact("retry"))));
    let second_retried = accepted_value(second.apply(input(3, compact("retry"))));
    let first_retry_action_id = dispatched_action_id(&first_retried).to_string();
    let second_retry_action_id = dispatched_action_id(&second_retried).to_string();

    // Assert
    Uuid::parse_str(&first_action_id).expect("initial compaction action ID is a UUID");
    Uuid::parse_str(&first_retry_action_id).expect("retry compaction action ID is a UUID");
    assert_eq!(first_action_id, second_action_id);
    assert_eq!(first_retry_action_id, second_retry_action_id);
    assert_ne!(first_action_id, first_retry_action_id);
}

///
/// *Prepare*: A completed user turn has a pending manual compaction checkpoint.
/// *Do*: Restore the checkpoint, defer the same notification in both sessions, accept the summary, and start the next turn.
/// *Assert*: Both sessions compact successfully and their next model inputs contain the preserved context, summary, and deferred notification.
///
#[test]
fn manual_compaction_survives_restore_and_replaces_model_context() {
    // Prepare
    let config = config();
    let mut live = HarnessSession::create(config.clone()).expect("session creates");
    let first_started = accepted_value(live.apply(input(
        1,
        user_message(
            "turn-before-compaction",
            "existing implementation context",
            "queue",
        ),
    )));
    let first_action_id = dispatched_action_id(&first_started).to_string();
    accepted_value(live.apply(input(
        2,
        completion(
            &first_action_id,
            json!([{"type": "text", "text": "first answer"}]),
        ),
    )));
    let requested = accepted_value(live.apply(input(3, compact("Preserve touched file paths."))));
    let compaction_action = dispatched_action(&requested);
    let compaction_action_id = compaction_action["action_id"]
        .as_str()
        .expect("compaction action has an ID")
        .to_string();
    let checkpoint = live.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");
    let summary = summary_completion(&compaction_action_id, "preserved manual state");

    // Do
    let live_notified = accepted_value(live.apply(input(
        4,
        notification(
            "manual-compaction-notification",
            "manual compaction finished",
        ),
    )));
    let restored_notified = accepted_value(restored.apply(input(
        1,
        notification(
            "manual-compaction-notification",
            "manual compaction finished",
        ),
    )));
    let live_compacted = accepted_value(live.apply(input(5, summary.clone())));
    let restored_compacted = accepted_value(restored.apply(input(2, summary)));
    let live_started = accepted_value(live.apply(input(
        6,
        user_message("turn-after-compaction", "continue", "queue"),
    )));
    let restored_started = accepted_value(restored.apply(input(
        3,
        user_message("turn-after-compaction", "continue", "queue"),
    )));

    // Assert
    assert_eq!(requested["transition"]["turn"]["status"], "compacting");
    assert_eq!(requested["transition"]["turn"]["trigger"], "manual");
    assert_eq!(compaction_action["type"], "llm_call");
    assert_eq!(compaction_action["purpose"], "compaction");
    assert_eq!(compaction_action["trigger"], "manual");
    assert!(serialized_messages(compaction_action).contains("Preserve touched file paths."));
    for notified in [&live_notified, &restored_notified] {
        assert_eq!(
            notified["transition"]["next"]["directives"][0]["type"],
            "keep"
        );
        assert_eq!(
            notified["transition"]["next"]["directives"][0]["action_id"],
            compaction_action_id
        );
    }
    assert_eq!(
        live_compacted["transition"]["observations"],
        restored_compacted["transition"]["observations"]
    );
    assert_eq!(
        live_compacted["transition"]["observations"][0]["type"],
        "context_compacted"
    );
    assert_eq!(live_compacted["transition"]["turn"]["status"], "idle");
    assert_eq!(restored_compacted["transition"]["turn"]["status"], "idle");
    assert_eq!(checkpoint_value(&live), checkpoint_value(&restored));

    let live_action = dispatched_action(&live_started);
    let restored_action = dispatched_action(&restored_started);
    assert_eq!(live_action["purpose"], "agent");
    assert_eq!(restored_action["purpose"], "agent");
    assert_eq!(live_action["model_input"]["messages"]["type"], "replace");
    assert_eq!(
        restored_action["model_input"]["messages"]["type"],
        "replace"
    );
    assert_eq!(model_messages(live_action), model_messages(restored_action));
    let messages = serialized_messages(restored_action);
    assert!(messages.contains("existing implementation context"));
    assert!(messages.contains("preserved manual state"));
    assert!(messages.contains("manual compaction finished"));
    assert!(messages.contains("continue"));
}

///
/// *Prepare*: A manual compaction is pending over an existing context message.
/// *Do*: Fail it, retry compaction with new instructions, and accept the retry summary.
/// *Assert*: Failure returns to idle with prior context preserved, and the retry replaces the stale prompt before succeeding.
///
#[test]
fn failed_manual_compaction_preserves_context_and_can_be_retried() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let first_started = accepted_value(session.apply(input(
        1,
        user_message("turn-before-failure", "existing context", "queue"),
    )));
    let first_action_id = dispatched_action_id(&first_started).to_string();
    accepted_value(session.apply(input(
        2,
        completion(
            &first_action_id,
            json!([{"type": "text", "text": "first done"}]),
        ),
    )));
    let requested = accepted_value(session.apply(input(3, compact("first attempt"))));
    let failed_action_id = dispatched_action_id(&requested).to_string();

    // Do
    let failed = accepted_value(session.apply(input(
        4,
        completion_failed(&failed_action_id, "summary failed"),
    )));
    let retried = accepted_value(session.apply(input(5, compact("retry without tool calls"))));
    let retry_action = dispatched_action(&retried);
    let retry_action_id = retry_action["action_id"]
        .as_str()
        .expect("retry action has an ID")
        .to_string();
    let compacted = accepted_value(session.apply(input(
        6,
        summary_completion(&retry_action_id, "retry summary"),
    )));
    let next_turn = accepted_value(session.apply(input(
        7,
        user_message("turn-after-retry", "continue", "queue"),
    )));

    // Assert
    assert_eq!(failed["transition"]["turn"]["status"], "idle");
    assert_eq!(failed["transition"]["next"]["type"], "none");
    assert_eq!(
        failed["transition"]["observations"][0]["type"],
        "context_compaction_failed"
    );
    assert_ne!(retry_action_id, failed_action_id);
    assert_eq!(retry_action["purpose"], "compaction");
    assert_eq!(retry_action["model_input"]["messages"]["type"], "replace");
    let retry_messages = serialized_messages(retry_action);
    assert!(retry_messages.contains("retry without tool calls"));
    assert!(!retry_messages.contains("first attempt"));
    assert_eq!(compacted["transition"]["turn"]["status"], "idle");
    assert_eq!(
        compacted["transition"]["observations"][0]["type"],
        "context_compacted"
    );
    let next_messages = serialized_messages(dispatched_action(&next_turn));
    assert!(next_messages.contains("existing context"));
    assert!(next_messages.contains("retry summary"));
}

///
/// *Prepare*: A manual compaction completion is pending in an otherwise idle session.
/// *Do*: Submit a new user turn before the compaction finishes.
/// *Assert*: Core rejects the overlapping turn without consuming its input ID or changing the pending checkpoint.
///
#[test]
fn manual_compaction_rejects_an_overlapping_user_turn() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    accepted_value(session.apply(input(1, compact("preserve recent context"))));
    let before_checkpoint = checkpoint_value(&session);

    // Do
    let rejected = serde_json::to_value(session.apply(input(
        2,
        user_message("turn-overlap", "start new work", "queue"),
    )))
    .expect("rejection serializes");

    // Assert
    assert_eq!(
        rejected,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "invalid_state",
                "command_type": "user_message",
                "state": "compacting",
            },
        })
    );
    assert_eq!(checkpoint_value(&session), before_checkpoint);
    assert_eq!(inspection_value(&session)["last_input_id"], 1);
}

///
/// *Prepare*: A completed session has synchronized model-message and tool-catalog caches.
/// *Do*: Reconfigure system instructions, then capabilities, then submit invalid settings and replay the prior receipt.
/// *Assert*: Each changed cache is replaced when required, while invalid configuration is non-consuming and atomic.
///
#[test]
fn reconfiguration_updates_caches_and_rejects_atomically() {
    // Prepare
    let base_config = config();
    let mut session = HarnessSession::create(base_config.clone()).expect("session creates");
    let first_started =
        accepted_value(session.apply(input(1, user_message("turn-one", "first", "queue"))));
    let first_action_id = dispatched_action_id(&first_started).to_string();
    accepted_value(session.apply(input(
        2,
        completion(
            &first_action_id,
            json!([{"type": "text", "text": "first done"}]),
        ),
    )));

    let mut capabilities =
        serde_json::to_value(&base_config.capabilities).expect("capabilities serialize");
    capabilities["tool_groups"]
        .as_array_mut()
        .expect("tool groups are an array")
        .push(json!({
            "name": "workspace",
            "description": "Workspace tools",
            "tools": [{
                "name": "lookup",
                "description": "Lookup",
                "input_schema": {"type": "object"},
                "exposure": "direct",
            }],
        }));
    let mut invalid_settings =
        serde_json::to_value(&base_config.settings).expect("settings serialize");
    invalid_settings["turn"]["max_iterations"] = json!(0);

    // Do
    accepted_value(session.apply(input(
        3,
        reconfigure(json!([{
            "type": "system_instructions",
            "value": "current reconfigured instructions",
        }])),
    )));
    let second_started =
        accepted_value(session.apply(input(4, user_message("turn-two", "second", "queue"))));
    let second_action_id = dispatched_action_id(&second_started).to_string();
    accepted_value(session.apply(input(
        5,
        completion(
            &second_action_id,
            json!([{"type": "text", "text": "second done"}]),
        ),
    )));

    accepted_value(session.apply(input(
        6,
        reconfigure(json!([{
            "type": "capabilities",
            "value": capabilities,
        }])),
    )));
    let third_started =
        accepted_value(session.apply(input(7, user_message("turn-three", "third", "queue"))));
    let third_action_id = dispatched_action_id(&third_started).to_string();
    let third_completion_input = input(
        8,
        completion(
            &third_action_id,
            json!([{"type": "text", "text": "third done"}]),
        ),
    );
    let third_completed = accepted_value(session.apply(third_completion_input.clone()));

    let invalid = serde_json::to_value(session.apply(input(
        9,
        reconfigure(json!([{
            "type": "settings",
            "value": invalid_settings,
        }])),
    )))
    .expect("rejection serializes");
    let replayed = serde_json::to_value(session.apply(third_completion_input))
        .expect("cached transition serializes");
    let fourth_started =
        accepted_value(session.apply(input(9, user_message("turn-four", "fourth", "queue"))));

    // Assert
    let second_action = dispatched_action(&second_started);
    assert_eq!(second_action["model_input"]["messages"]["type"], "replace");
    assert_eq!(second_action["model_input"]["tool_catalog"]["type"], "keep");
    let second_messages = serialized_messages(second_action);
    assert!(second_messages.contains("current reconfigured instructions"));

    let third_action = dispatched_action(&third_started);
    assert_eq!(third_action["model_input"]["messages"]["type"], "replace");
    assert_eq!(
        third_action["model_input"]["tool_catalog"]["type"],
        "replace"
    );
    assert_eq!(third_action["model_input"]["tool_catalog"]["revision"], 1);
    assert!(
        third_action["model_input"]["tool_catalog"]["tools"]
            .as_array()
            .expect("tool catalog is an array")
            .iter()
            .any(|tool| tool["name"] == "lookup")
    );

    assert_eq!(invalid["type"], "rejected");
    assert_eq!(invalid["rejection"]["code"], "invalid_command");
    assert_eq!(
        invalid["rejection"]["error"]["message"],
        "settings.turn.max_iterations must be greater than zero"
    );
    assert_eq!(replayed, third_completed);
    let fourth_action = dispatched_action(&fourth_started);
    assert_eq!(
        fourth_action["max_iterations"],
        json!(base_config.settings.turn.max_iterations)
    );
    assert_eq!(fourth_action["model_input"]["messages"]["type"], "append");
    assert_eq!(fourth_action["model_input"]["tool_catalog"]["type"], "keep");
    assert_eq!(fourth_action["model_input"]["tool_catalog"]["revision"], 1);
}

///
/// *Prepare*: A completed Unix session has synchronized model-message and tool-catalog caches with background processes enabled.
/// *Do*: Reconfigure the complete settings to PowerShell and start another turn.
/// *Assert*: Core replaces both caches with PowerShell command names and foreground/background syntax guidance while retaining the existing Runtime route contract.
///
#[test]
fn command_environment_reconfiguration_replaces_prompt_and_tool_catalog() {
    // Prepare
    let mut base_config = config();
    base_config.settings.tools.background_processes = BackgroundProcessMode::Enabled;
    let mut settings = serde_json::to_value(&base_config.settings).unwrap();
    settings["tools"]["command_environment"] = json!({"mode": "powershell"});
    let mut session = HarnessSession::create(base_config).expect("session creates");
    let first_started = accepted_value(session.apply(input(
        1,
        user_message("turn-command-unix", "run a command", "queue"),
    )));
    let first_action_id = dispatched_action_id(&first_started).to_string();
    accepted_value(session.apply(input(
        2,
        completion(
            &first_action_id,
            json!([{"type": "text", "text": "unix done"}]),
        ),
    )));

    // Do
    accepted_value(session.apply(input(
        3,
        reconfigure(json!([{"type": "settings", "value": settings}])),
    )));
    let second_started = accepted_value(session.apply(input(
        4,
        user_message("turn-command-powershell", "run another command", "queue"),
    )));

    // Assert
    let action = dispatched_action(&second_started);
    assert_eq!(action["model_input"]["messages"]["type"], "replace");
    assert_eq!(action["model_input"]["tool_catalog"]["type"], "replace");
    assert_eq!(action["model_input"]["tool_catalog"]["revision"], 1);
    let tool_names = action["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(tool_names.contains(&"bash"));
    assert!(!tool_names.contains(&"powershell"));
    let messages = serialized_messages(action);
    assert!(messages.contains("accepts the same command syntax as"));
    assert!(messages.contains("tools.file_system.bash"));
}
