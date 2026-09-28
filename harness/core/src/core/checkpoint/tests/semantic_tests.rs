use serde_json::json;

use super::cases::*;
use crate::core::checkpoint::{Checkpoint, decode_checkpoint};
use crate::core::testing::config;
use crate::core::{Message, StoredMessage, TurnState};

#[test]
fn checkpoint_serializes_as_a_flat_v1_document() {
    let state = crate::core::initial_state(config()).unwrap();
    let checkpoint = Checkpoint::capture(&state).unwrap();

    let encoded = serde_json::to_value(&checkpoint).unwrap();

    assert_eq!(
        encoded,
        json!({
            "checkpoint_version": 1,
            "compaction_count": 0,
            "context": {
                "messages": []
            },
            "turn": { "type": "idle" },
            "notifications": []
        })
    );
    assert_eq!(decode_checkpoint(&encoded.to_string()).unwrap(), checkpoint);
}

/// *Prepare*: Encode pending user steering and a model-ready notification while a pre-LLM hook is active.
/// *Do*: Decode and restore the checkpoint through the V1 conversion model, then capture it again.
/// *Assert*: Steering order, delivery readiness, notification identity, and wire data are unchanged.
#[test]
fn pending_steering_and_ready_notifications_restore_without_semantic_loss() {
    // Prepare
    let mut checkpoint = checkpoint_with_turn(json!({
        "type": "active",
        "turn_id": "turn-1",
        "iterations": 1,
        "phase": {
            "type": "awaiting_hook",
            "pending": {
                "hook": "pre_llm_call",
                "hook_binding_ids": ["binding-1"],
                "completion_action_id": test_completion_action_id()
            }
        },
        "queued": { "type": "empty" },
        "pending_steer": [{
            "message": {
                "role": "user",
                "content": [{ "type": "text", "text": "steer after the hook" }]
            },
            "source": "history"
        }],
        "model_input_ready": true
    }));
    checkpoint["context"]["messages"] = json!([{
        "message": {
            "role": "user",
            "content": [{ "type": "text", "text": "start work" }]
        },
        "source": "history"
    }]);
    checkpoint["notifications"] = json!([
        {
            "type": "ready",
            "notification": {
                "id": "notification-ready",
                "source": {
                    "type": "async_tool",
                    "call_id": "call-1",
                    "status": "completed"
                },
                "level": "info",
                "message": "background work completed"
            }
        },
        { "type": "received", "id": "notification-received" }
    ]);

    // Do
    let restored = decode_checkpoint(&checkpoint.to_string())
        .unwrap()
        .restore(config())
        .unwrap();
    let recaptured = serde_json::to_value(Checkpoint::capture(&restored).unwrap()).unwrap();

    // Assert
    let TurnState::Active(active) = &restored.turn else {
        panic!("expected active checkpoint");
    };
    assert_eq!(
        active.pending_steer,
        vec![StoredMessage::visible(Message::user_text(
            "steer after the hook"
        ))]
    );
    assert!(active.model_input_ready);
    assert!(restored.notifications.ready_for_model());
    let notifications = restored.notifications.pending().collect::<Vec<_>>();
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].id, "notification-ready");
    assert_eq!(recaptured, checkpoint);
}

#[test]
fn derived_identities_are_absent_from_json_and_reconstructed_exactly() {
    let direct_json = restorable_active_tool_checkpoint();
    let direct_state_json = &direct_json["turn"]["phase"]["batch"]["executions"][0];
    assert!(direct_state_json.get("hook_action_id").is_none());
    assert!(direct_state_json["call"].get("action_id").is_none());
    assert!(direct_state_json["call"].get("call_id").is_none());

    let direct_state = decode_checkpoint(&direct_json.to_string())
        .unwrap()
        .restore(config())
        .unwrap();
    let crate::core::TurnState::Active(active) = &direct_state.turn else {
        panic!("expected active direct-tool checkpoint");
    };
    let crate::core::ActivePhase::AwaitingToolBatch { batch, .. } = &active.phase else {
        panic!("expected direct tool batch");
    };
    let crate::core::ToolExecutionState::DirectAwaitingPostHook {
        hook_action_id,
        call,
        ..
    } = &batch.executions[0].state
    else {
        panic!("expected pending direct post-tool hook");
    };
    let direct_action_id = crate::core::tools::external::effect_id_for_operation(
        crate::core::ToolOrigin::TopLevel,
        "call-1",
    );
    assert_eq!(call.call_id, "call-1");
    assert_eq!(call.action_id, direct_action_id);
    assert_eq!(
        hook_action_id,
        &crate::core::hooks::hook_action_id(
            &direct_action_id,
            crate::core::HookPoint::PostToolCall,
        )
    );
    assert_eq!(
        serde_json::to_value(Checkpoint::capture(&direct_state).unwrap()).unwrap(),
        direct_json
    );

    let program_json = restorable_pending_program_checkpoint();
    let program_execution_json = &program_json["turn"]["phase"]["batch"]["executions"][0]["execution"]
        ["operations"][0]["execution"];
    assert!(program_execution_json.get("hook_action_id").is_none());
    assert!(program_execution_json["call"].get("action_id").is_none());
    assert!(program_execution_json["call"].get("call_id").is_none());

    let program_state = decode_checkpoint(&program_json.to_string())
        .unwrap()
        .restore(config())
        .unwrap();
    let crate::core::TurnState::Active(active) = &program_state.turn else {
        panic!("expected active program checkpoint");
    };
    let crate::core::ActivePhase::AwaitingToolBatch { batch, .. } = &active.phase else {
        panic!("expected program tool batch");
    };
    let crate::core::ToolExecutionState::ProgramPending { execution } = &batch.executions[0].state
    else {
        panic!("expected pending program execution");
    };
    let capture = execution.capture().unwrap();
    let crate::core::features::programmatic_tool_calling::ProgramOperationRecord::ExternalPending {
        id,
        pending,
        ..
    } = &capture.operations()[0]
    else {
        panic!("expected pending external program operation");
    };
    let program_action_id = crate::core::tools::external::effect_id_for_operation(
        crate::core::ToolOrigin::Programmatic,
        "operation-1",
    );
    assert_eq!(id, "operation-1");
    let crate::core::features::programmatic_tool_calling::PendingProgramRecord::AwaitingPreHook {
        call,
        hook_action_id,
        ..
    } = pending.as_ref()
    else {
        panic!("expected pending program pre-tool hook");
    };
    assert_eq!(call.call_id, "operation-1");
    assert_eq!(call.action_id, program_action_id);
    assert_eq!(
        hook_action_id,
        &crate::core::hooks::hook_action_id(
            &program_action_id,
            crate::core::HookPoint::PreToolCall,
        )
    );
    assert_eq!(
        serde_json::to_value(Checkpoint::capture(&program_state).unwrap()).unwrap(),
        program_json
    );
}

#[test]
fn programmatic_calls_validate_against_canonical_runtime_names() {
    let cases = [
        (
            "renamed Runtime built-in",
            "process_start",
            json!({
                "type": "runtime_builtin",
                "name": "process.start",
                "arguments": { "command": "cargo test" }
            }),
            json!({ "command": "cargo test" }),
        ),
        (
            "provided tool",
            "provided_tool::calendar::list_events",
            json!({
                "type": "provided",
                "group_name": "calendar",
                "tool_name": "list_events",
                "arguments": { "calendar_id": "work" }
            }),
            json!({ "calendar_id": "work" }),
        ),
    ];

    for (label, runtime_name, call, arguments) in cases {
        let mut checkpoint = restorable_pending_program_checkpoint();
        let operation = &mut checkpoint["turn"]["phase"]["batch"]["executions"][0]["execution"]["operations"]
            [0];
        operation["function"]["name"] = json!(runtime_name);
        operation["function"]["arguments"] = arguments;
        operation["execution"]["call"] = call;

        let decoded = decode_checkpoint(&checkpoint.to_string()).unwrap();
        let restored = decoded
            .restore(config())
            .unwrap_or_else(|error| panic!("failed to restore {label}: {error}"));

        assert_eq!(
            serde_json::to_value(Checkpoint::capture(&restored).unwrap()).unwrap(),
            checkpoint,
            "changed {label} checkpoint during semantic round trip"
        );
    }
}

#[test]
fn lifecycle_hook_identities_are_reconstructed_from_their_parent() {
    let post_llm_json = restorable_active_candidate_checkpoint();
    assert!(
        post_llm_json["turn"]["phase"]["pending"]
            .get("action_id")
            .is_none()
    );
    let post_llm_state = decode_checkpoint(&post_llm_json.to_string())
        .unwrap()
        .restore(config())
        .unwrap();
    let crate::core::TurnState::Active(active) = &post_llm_state.turn else {
        panic!("expected active lifecycle-hook checkpoint");
    };
    let crate::core::ActivePhase::AwaitingHook { pending } = &active.phase else {
        panic!("expected pending lifecycle hook");
    };
    let crate::core::PendingLifecycleHook::PostLlmCall {
        action_id,
        completion_action_id,
        ..
    } = pending
    else {
        panic!("expected post-LLM hook");
    };
    assert_eq!(completion_action_id, &test_completion_action_id());
    assert_eq!(
        action_id,
        &crate::core::hooks::hook_action_id(
            completion_action_id,
            crate::core::HookPoint::PostLlmCall,
        )
    );
    assert_eq!(
        serde_json::to_value(Checkpoint::capture(&post_llm_state).unwrap()).unwrap(),
        post_llm_json
    );

    let mut wrong_hook_id = post_llm_state.clone();
    let crate::core::TurnState::Active(active) = &mut wrong_hook_id.turn else {
        panic!("expected active lifecycle-hook checkpoint");
    };
    let crate::core::ActivePhase::AwaitingHook {
        pending: crate::core::PendingLifecycleHook::PostLlmCall { action_id, .. },
    } = &mut active.phase
    else {
        panic!("expected post-LLM hook");
    };
    *action_id = "other-hook".to_string();
    assert!(Checkpoint::capture(&wrong_hook_id).is_err());

    let pre_agent_json = checkpoint_with_turn(json!({
        "type": "active",
        "turn_id": "turn-1",
        "iterations": 0,
        "phase": {
            "type": "awaiting_hook",
            "pending": {
                "hook": "pre_agent_turn",
                "hook_binding_ids": ["binding-1"],
                "user_content": [{ "type": "text", "text": "work" }],
                "previous_turn": { "type": "idle" }
            }
        },
        "queued": { "type": "empty" },
        "pending_steer": [],
        "model_input_ready": false
    }));
    let pre_agent_state = decode_checkpoint(&pre_agent_json.to_string())
        .unwrap()
        .restore(config())
        .unwrap();
    let crate::core::TurnState::Active(active) = &pre_agent_state.turn else {
        panic!("expected active pre-agent checkpoint");
    };
    let crate::core::ActivePhase::AwaitingHook {
        pending:
            crate::core::PendingLifecycleHook::PreAgentTurn {
                action_id, turn_id, ..
            },
    } = &active.phase
    else {
        panic!("expected pre-agent hook");
    };
    assert_eq!(turn_id, &active.turn_id);
    assert_eq!(
        action_id,
        &crate::core::hooks::hook_action_id(&active.turn_id, crate::core::HookPoint::PreAgentTurn,)
    );
    assert_eq!(
        serde_json::to_value(Checkpoint::capture(&pre_agent_state).unwrap()).unwrap(),
        pre_agent_json
    );

    let mut legacy_completion_id = restorable_active_candidate_checkpoint();
    legacy_completion_id["turn"]["phase"]["pending"]["completion_action_id"] =
        json!("completion:task:0:1");
    let restored = decode_checkpoint(&legacy_completion_id.to_string())
        .unwrap()
        .restore(config())
        .unwrap();
    assert_eq!(
        serde_json::to_value(Checkpoint::capture(&restored).unwrap()).unwrap(),
        legacy_completion_id
    );
}

#[test]
fn capture_rejects_noncanonical_derived_tool_identities() {
    let state = decode_checkpoint(&restorable_active_tool_checkpoint().to_string())
        .unwrap()
        .restore(config())
        .unwrap();

    let mut wrong_call_id = state.clone();
    let crate::core::TurnState::Active(active) = &mut wrong_call_id.turn else {
        panic!("expected active checkpoint");
    };
    let crate::core::ActivePhase::AwaitingToolBatch { batch, .. } = &mut active.phase else {
        panic!("expected tool batch");
    };
    let crate::core::ToolExecutionState::DirectAwaitingPostHook { call, .. } =
        &mut batch.executions[0].state
    else {
        panic!("expected pending direct hook");
    };
    call.call_id = "other-call".to_string();
    assert!(Checkpoint::capture(&wrong_call_id).is_err());

    let mut wrong_action_id = state.clone();
    let crate::core::TurnState::Active(active) = &mut wrong_action_id.turn else {
        panic!("expected active checkpoint");
    };
    let crate::core::ActivePhase::AwaitingToolBatch { batch, .. } = &mut active.phase else {
        panic!("expected tool batch");
    };
    let crate::core::ToolExecutionState::DirectAwaitingPostHook { call, .. } =
        &mut batch.executions[0].state
    else {
        panic!("expected pending direct hook");
    };
    call.action_id = "other-action".to_string();
    assert!(Checkpoint::capture(&wrong_action_id).is_err());

    let mut wrong_hook_action_id = state;
    let crate::core::TurnState::Active(active) = &mut wrong_hook_action_id.turn else {
        panic!("expected active checkpoint");
    };
    let crate::core::ActivePhase::AwaitingToolBatch { batch, .. } = &mut active.phase else {
        panic!("expected tool batch");
    };
    let crate::core::ToolExecutionState::DirectAwaitingPostHook { hook_action_id, .. } =
        &mut batch.executions[0].state
    else {
        panic!("expected pending direct hook");
    };
    *hook_action_id = "other-hook".to_string();
    assert!(Checkpoint::capture(&wrong_hook_action_id).is_err());
}
