//! Characterization tests for the interface between the Runtime and one Core
//! session. These tests use serialized Step Protocol values so private state and
//! helper functions can move without weakening the behavior they protect.

use serde_json::Value;
use serde_json::json;

use crate::core::HarnessApplyResult;
use crate::core::HarnessConfig;
use crate::core::HarnessInput;
use crate::core::HarnessSession;
use crate::core::HookPoint;
use crate::core::decode_checkpoint;

mod acceptance_scenarios;
mod background_processes;
mod compaction_and_reconfiguration;
mod filesystem;
mod history_and_rejections;
mod lifecycle_hooks;
mod programmatic_tools;
mod skills;
mod subagents;
mod tools_and_hooks;

fn config() -> HarnessConfig {
    let mut config = crate::core::testing::config();
    config.task_id = "public-interface-tests".to_string();
    config
}

fn config_with_post_llm_hook() -> HarnessConfig {
    let mut config = config();
    crate::core::testing::add_always_hook(&mut config, HookPoint::PostLlmCall);
    config
}

fn input(input_id: u64, command: Value) -> HarnessInput {
    serde_json::from_value(json!({
        "protocol_version": 1,
        "input_id": input_id,
        "determinism": {
            "time_unix_ms": 1_700_000_000_000_u64 + input_id,
            "random_seed": input_id,
        },
        "command": command,
    }))
    .expect("public-interface test input is valid")
}

fn user_message(turn_id: &str, text: &str, mode: &str) -> Value {
    json!({
        "type": "user_message",
        "turn_id": turn_id,
        "mode": mode,
        "content": [{"type": "text", "text": text}],
    })
}

fn completion(action_id: &str, parts: Value) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id,
        "result": {
            "parts": parts,
            "finish_reason": "stop",
            "usage": null,
        },
    })
}

fn notification(id: &str, message: &str) -> Value {
    json!({
        "type": "notification",
        "notification": {
            "id": id,
            "source": {
                "type": "background_process",
                "process_id": "process-1",
                "status": "completed",
                "exit_code": 0,
            },
            "level": "info",
            "message": message,
            "content": [],
        },
    })
}

fn accepted_value(result: HarnessApplyResult) -> Value {
    let value = serde_json::to_value(result).expect("apply result serializes");
    assert_eq!(value["type"], "accepted", "Core rejected command: {value}");
    value
}

fn dispatched_action_id(result: &Value) -> &str {
    result["transition"]["next"]["directives"][0]["action"]["action_id"]
        .as_str()
        .expect("accepted transition dispatches one action")
}

fn checkpoint_value(session: &HarnessSession) -> Value {
    serde_json::to_value(session.checkpoint().expect("checkpoint captures"))
        .expect("checkpoint serializes")
}

fn inspection_value(session: &HarnessSession) -> Value {
    serde_json::to_value(session.inspect()).expect("inspection serializes")
}

fn abandoned_actions(result: &Value) -> Vec<(&str, &str)> {
    result["transition"]["observations"]
        .as_array()
        .expect("observations are an array")
        .iter()
        .filter(|observation| observation["type"] == "action_abandoned")
        .map(|observation| {
            (
                observation["action_id"]
                    .as_str()
                    .expect("abandoned action has an ID"),
                observation["cause"]
                    .as_str()
                    .expect("abandoned action has a cause"),
            )
        })
        .collect()
}

///
/// *Prepare*: A new session has no active turn or accepted Step Protocol input.
/// *Do*: Inspect it before, during, and after one completed text turn.
/// *Assert*: Each serialized inspection reports the public lifecycle, pending action, revisions, and input position.
///
#[test]
fn inspection_serializes_the_idle_running_and_completed_lifecycle() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let idle = inspection_value(&session);

    // Do
    let started = accepted_value(session.apply(input(1, user_message("turn-1", "work", "queue"))));
    let action_id = dispatched_action_id(&started).to_string();
    let running = inspection_value(&session);
    let completed = accepted_value(session.apply(input(
        2,
        completion(&action_id, json!([{"type": "text", "text": "done"}])),
    )));
    let terminal = inspection_value(&session);

    // Assert
    assert_eq!(
        idle,
        json!({
            "protocol_version": 1,
            "status": "idle",
            "active_turn_id": null,
            "last_turn_id": null,
            "pending_actions": [],
            "message_count": 0,
            "context_revision": 0,
            "tool_catalog_revision": 0,
            "last_input_id": 0,
        })
    );
    assert_eq!(
        running,
        json!({
            "protocol_version": 1,
            "status": "running",
            "active_turn_id": "turn-1",
            "last_turn_id": null,
            "pending_actions": [{
                "type": "completion",
                "purpose": "agent",
                "action_id": action_id,
            }],
            "message_count": 2,
            "context_revision": 1,
            "tool_catalog_revision": 0,
            "last_input_id": 1,
        })
    );
    assert_eq!(completed["transition"]["turn"]["status"], "completed");
    assert_eq!(
        terminal,
        json!({
            "protocol_version": 1,
            "status": "completed",
            "active_turn_id": null,
            "last_turn_id": "turn-1",
            "pending_actions": [],
            "message_count": 3,
            "context_revision": 2,
            "tool_catalog_revision": 0,
            "last_input_id": 2,
        })
    );
}

///
/// *Prepare*: A session has one pending external tool action and a serialized checkpoint.
/// *Do*: Restore the checkpoint, inspect it, and return the tool result as the restored session's first input.
/// *Assert*: The pending action and semantic checkpoint survive, while delivery revisions and input position restart before a full model-input replacement.
///
#[test]
fn restore_preserves_a_pending_tool_continuation_and_restarts_step_delivery() {
    // Prepare
    let config = config();
    let mut original = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(original.apply(input(
        1,
        user_message("turn-tool", "read the file", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let tool_pending = accepted_value(original.apply(input(
        2,
        json!({
            "type": "completion_succeeded",
            "action_id": completion_action_id,
            "result": {
                "parts": [{
                    "type": "tool_call",
                    "id": "call-read",
                    "name": "read_file",
                    "arguments_json": "{\"path\":\"notes.txt\"}"
                }],
                "finish_reason": "tool_call",
                "usage": null
            }
        }),
    )));
    let tool_action = &tool_pending["transition"]["next"]["directives"][0]["action"];
    let tool_action_id = tool_action["action_id"]
        .as_str()
        .expect("tool action has an ID")
        .to_string();
    let call_id = tool_action["call_id"]
        .as_str()
        .expect("tool action has a call ID")
        .to_string();
    let original_inspection = inspection_value(&original);
    let checkpoint = original.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");

    // Do
    let mut restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");
    let restored_inspection = inspection_value(&restored);
    let restored_checkpoint = checkpoint_value(&restored);
    let resumed = accepted_value(restored.apply(input(
        1,
        json!({
            "type": "tool_succeeded",
            "action_id": tool_action_id,
            "call_id": call_id,
            "result": {
                "type": "success",
                "content": [{"type": "text", "text": "file contents"}]
            }
        }),
    )));

    // Assert
    assert_eq!(restored_checkpoint, checkpoint_value(&original));
    assert_eq!(
        original_inspection["pending_actions"],
        json!([{
            "type": "runtime_builtin_tool_call",
            "action_id": tool_action_id,
            "call_id": call_id,
            "name": "file_system.read_file",
        }])
    );
    let mut expected_restored_inspection = original_inspection;
    expected_restored_inspection["context_revision"] = json!(0);
    expected_restored_inspection["last_input_id"] = json!(0);
    assert_eq!(restored_inspection, expected_restored_inspection);
    assert_eq!(resumed["transition"]["input_id"], 1);
    assert_eq!(
        resumed["transition"]["observations"][0]["type"],
        "tool_result_committed"
    );
    let next_action = &resumed["transition"]["next"]["directives"][0]["action"];
    assert_eq!(next_action["type"], "llm_call");
    assert_eq!(next_action["model_input"]["messages"]["type"], "replace");
    assert_eq!(
        next_action["model_input"]["tool_catalog"]["type"],
        "replace"
    );
}

///
/// *Prepare*: A running session is checkpointed under one set of system instructions.
/// *Do*: Restore it with new instructions and request a full model-input refresh.
/// *Assert*: The refresh keeps historical user context but renders the current configuration.
///
#[test]
fn restore_regenerates_model_input_from_the_current_configuration() {
    // Prepare
    let mut original_config = config();
    original_config.system_instructions = "obsolete restore instructions".to_string();
    let mut original = HarnessSession::create(original_config).expect("original session creates");
    let started = accepted_value(original.apply(input(
        1,
        user_message("turn-config", "preserved user context", "queue"),
    )));
    let action_id = dispatched_action_id(&started).to_string();
    let checkpoint = original.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut current_config = config();
    current_config.system_instructions = "current restore instructions".to_string();

    // Do
    let mut restored =
        HarnessSession::restore(current_config, decoded, 0).expect("checkpoint restores");
    let refreshed = accepted_value(restored.apply(input(
        1,
        json!({
            "type": "completion_model_input_resync_requested",
            "action_id": action_id,
        }),
    )));

    // Assert
    let messages = &refreshed["transition"]["next"]["directives"][0]["action"]["model_input"]["messages"]
        ["messages"];
    let encoded_messages = serde_json::to_string(messages).expect("model messages serialize");
    assert!(encoded_messages.contains("current restore instructions"));
    assert!(!encoded_messages.contains("obsolete restore instructions"));
    assert!(encoded_messages.contains("preserved user context"));
}

///
/// *Prepare*: An idle session accepts one Runtime notification.
/// *Do*: Deliver the same notification ID again, then start a user turn.
/// *Assert*: The duplicate changes no semantic state and the next model input contains one notification message.
///
#[test]
fn idle_notification_is_injected_once_and_duplicate_is_a_noop() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let received =
        accepted_value(session.apply(input(1, notification("notification-1", "build done"))));
    let before_duplicate = checkpoint_value(&session);

    // Do
    let duplicate =
        accepted_value(session.apply(input(2, notification("notification-1", "build done"))));
    let after_duplicate = checkpoint_value(&session);
    let started = accepted_value(session.apply(input(
        3,
        user_message("turn-notification", "report status", "queue"),
    )));

    // Assert
    assert_eq!(received["transition"]["next"]["type"], "none");
    assert_eq!(
        received["transition"]["observations"][0]["type"],
        "notification_received"
    );
    assert_eq!(duplicate["transition"]["next"]["type"], "none");
    assert_eq!(duplicate["transition"]["observations"], json!([]));
    assert_eq!(after_duplicate, before_duplicate);
    let messages = &started["transition"]["next"]["directives"][0]["action"]["model_input"]["messages"]
        ["messages"];
    let encoded_messages = serde_json::to_string(messages).expect("model messages serialize");
    assert_eq!(encoded_messages.matches("build done").count(), 1);
}

///
/// *Prepare*: A notification is deferred while a completion action is pending, then the Core is checkpointed.
/// *Do*: Complete the action in both the live and restored sessions.
/// *Assert*: Both sessions continue identically and append the deferred notification to the next model input.
///
#[test]
fn pending_notification_survives_restore_and_enters_next_model_input() {
    // Prepare
    let config = config();
    let mut original = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(original.apply(input(
        1,
        user_message("turn-notification", "wait for status", "queue"),
    )));
    let action_id = dispatched_action_id(&started).to_string();
    let notified = accepted_value(
        original.apply(input(2, notification("notification-1", "process finished"))),
    );
    let checkpoint = original.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");
    let completed = completion(
        &action_id,
        json!([{"type": "text", "text": "initial answer"}]),
    );

    // Do
    let live = accepted_value(original.apply(input(3, completed.clone())));
    let resumed = accepted_value(restored.apply(input(1, completed)));

    // Assert
    assert_eq!(
        notified["transition"]["next"]["directives"][0]["type"],
        "keep"
    );
    assert_eq!(
        notified["transition"]["next"]["directives"][0]["action_id"],
        action_id
    );
    assert_eq!(
        live["transition"]["observations"],
        resumed["transition"]["observations"]
    );
    assert_eq!(live["transition"]["turn"], resumed["transition"]["turn"]);
    assert_eq!(checkpoint_value(&original), checkpoint_value(&restored));
    let live_action = &live["transition"]["next"]["directives"][0]["action"];
    let resumed_action = &resumed["transition"]["next"]["directives"][0]["action"];
    assert_eq!(live_action["action_id"], resumed_action["action_id"]);
    assert_eq!(live_action["turn_id"], resumed_action["turn_id"]);
    assert_eq!(live_action["purpose"], resumed_action["purpose"]);
    assert_eq!(live_action["model_input"]["messages"]["type"], "append");
    assert_eq!(resumed_action["model_input"]["messages"]["type"], "replace");
    let live_messages = live_action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("live model messages are an array");
    let resumed_messages = resumed_action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("restored model messages are an array");
    assert_eq!(
        &resumed_messages[resumed_messages.len() - live_messages.len()..],
        live_messages
    );
    let encoded_messages =
        serde_json::to_string(resumed_messages).expect("model messages serialize");
    assert!(encoded_messages.contains("initial answer"));
    assert!(encoded_messages.contains("process finished"));
}

///
/// *Prepare*: A session is waiting for a completion and its checkpoint is captured.
/// *Do*: Apply an invalid empty completion, then apply a valid completion with the same input ID.
/// *Assert*: The invalid command changes no checkpoint state and does not consume the input ID.
///
#[test]
fn rejected_commands_leave_checkpoint_state_and_input_sequence_unchanged() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let started = accepted_value(session.apply(input(1, user_message("turn-1", "work", "queue"))));
    let action_id = dispatched_action_id(&started).to_string();
    let before = checkpoint_value(&session);

    // Do
    let rejected = serde_json::to_value(session.apply(input(2, completion(&action_id, json!([])))))
        .expect("rejection serializes");
    let after_rejection = checkpoint_value(&session);
    let accepted = accepted_value(session.apply(input(
        2,
        completion(&action_id, json!([{"type": "text", "text": "done"}])),
    )));

    // Assert
    assert_eq!(rejected["type"], "rejected");
    assert_eq!(
        rejected["rejection"]["error"]["message"],
        "completion result must contain at least one part"
    );
    assert_eq!(before, after_rejection);
    assert_eq!(accepted["transition"]["input_id"], 2);
    assert_eq!(accepted["transition"]["turn"]["status"], "completed");
}

///
/// *Prepare*: A fresh session receives its first user message.
/// *Do*: Replay the byte-equivalent latest input.
/// *Assert*: Core returns the cached transition and does not change its checkpoint.
///
#[test]
fn duplicate_latest_input_returns_the_cached_transition() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let command = input(1, user_message("turn-1", "work", "queue"));
    let first = session.apply(command.clone());
    let before = checkpoint_value(&session);

    // Do
    let duplicate = session.apply(command);

    // Assert
    assert_eq!(duplicate, first);
    assert_eq!(checkpoint_value(&session), before);
}

///
/// *Prepare*: A session is waiting for its first completion.
/// *Do*: Interrupt the active turn.
/// *Assert*: The transition abandons exactly the pending completion with the interrupt cause.
///
#[test]
fn interruption_abandons_exactly_the_prior_action() {
    // Prepare
    let mut interrupted = HarnessSession::create(config()).expect("interrupt session creates");
    let interrupt_started = accepted_value(
        interrupted.apply(input(1, user_message("turn-interrupt", "work", "queue"))),
    );
    let interrupt_action_id = dispatched_action_id(&interrupt_started).to_string();

    // Do
    let interrupt_result = accepted_value(interrupted.apply(input(
        2,
        json!({
            "type": "interrupt",
            "expected_turn_id": "turn-interrupt",
            "reason": "stop",
        }),
    )));

    // Assert
    assert_eq!(
        abandoned_actions(&interrupt_result),
        vec![(interrupt_action_id.as_str(), "interrupt")]
    );
}

///
/// *Prepare*: A post-LLM hook is pending after a completion candidate, then Core is checkpointed.
/// *Do*: Restore the checkpoint and ask the hook to retry the completion.
/// *Assert*: Semantic continuation survives while disposable input and model caches restart cleanly.
///
#[test]
fn restore_preserves_semantic_continuation_but_resets_disposable_step_state() {
    // Prepare
    let config = config_with_post_llm_hook();
    let mut original = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(original.apply(input(1, user_message("turn-1", "work", "queue"))));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let hook_pending = accepted_value(original.apply(input(
        2,
        completion(
            &completion_action_id,
            json!([{"type": "text", "text": "draft"}]),
        ),
    )));
    let hook_action_id = dispatched_action_id(&hook_pending).to_string();
    let checkpoint = original.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");

    // Do
    let retried = accepted_value(restored.apply(input(
        1,
        json!({
            "type": "hook_completed",
            "action_id": hook_action_id,
            "result": {
                "hook": "post_llm_call",
                "output": {
                    "type": "retry",
                    "feedback": [{"type": "text", "text": "try again"}],
                },
            },
        }),
    )));

    // Assert
    assert_eq!(retried["transition"]["input_id"], 1);
    let action = &retried["transition"]["next"]["directives"][0]["action"];
    assert_eq!(action["type"], "llm_call");
    assert_eq!(action["model_input"]["messages"]["type"], "replace");
    assert_eq!(action["model_input"]["tool_catalog"]["type"], "replace");
}
