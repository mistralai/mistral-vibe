use serde_json::json;

use super::cases::{checkpoint_with_turn, restorable_active_tool_checkpoint};
use crate::core::checkpoint::{Checkpoint, decode_checkpoint};
use crate::core::features::compaction::{
    CompactionBudget, CompactionProjection, CompactionProjectionFit, CompactionTrigger,
    PendingCompaction,
};
use crate::core::testing::config;
use crate::core::{
    ActivePhase, AssistantPart, AssistantSemanticPart, Message, ToolArguments, ToolExecutionState,
    TurnState,
};

type HarnessStateMutation = fn(&mut crate::core::HarnessState);
type ProgramExecutionMutation = fn(&mut crate::core::ProgramExecution);

/// *Prepare*: Build live compaction tokens in lifecycle positions that normal Core transitions cannot create.
/// *Do*: Capture each malformed state through checkpoint V1.
/// *Assert*: Capture rejects the misplaced or mismatched feature state instead of persisting it.
#[test]
fn capture_rejects_compaction_state_that_disagrees_with_its_lifecycle() {
    let base = crate::core::testing::started();
    let cases: [(&str, HarnessStateMutation); 3] = [
        ("active identity mismatch", |state| {
            let projection = compaction_projection(state);
            let TurnState::Active(active) = &mut state.turn else {
                panic!("expected active turn");
            };
            active.phase = ActivePhase::AwaitingCompaction {
                pending: PendingCompaction::in_turn(
                    crate::core::action_id::compaction("task", 1),
                    "different-turn".to_string(),
                    active.iterations,
                    projection,
                ),
            };
        }),
        ("between-turn token inside active turn", |state| {
            let projection = compaction_projection(state);
            let TurnState::Active(active) = &mut state.turn else {
                panic!("expected active turn");
            };
            active.phase = ActivePhase::AwaitingCompaction {
                pending: PendingCompaction::between_turns(
                    crate::core::action_id::compaction("task", 1),
                    CompactionTrigger::Manual,
                    projection,
                ),
            };
        }),
        ("in-turn token between turns", |state| {
            let projection = compaction_projection(state);
            state.turn = TurnState::Compacting {
                pending: PendingCompaction::in_turn(
                    crate::core::action_id::compaction("task", 1),
                    "work".to_string(),
                    0,
                    projection,
                ),
            };
        }),
    ];

    for (label, mutate) in cases {
        // Prepare
        let mut state = base.clone();
        mutate(&mut state);

        // Do
        let error = Checkpoint::capture(&state).unwrap_err();

        // Assert
        assert!(error.contains("compaction"), "{label}: {error}");
    }
}

fn compaction_projection(state: &crate::core::HarnessState) -> CompactionProjection {
    let projection = CompactionProjection::initial(
        state.context.messages(),
        state.generated_system_message(),
        "",
        state.resolved_tools.top_level_tools(),
        CompactionBudget {
            token_threshold: state.config.settings.context.compaction.token_threshold(),
            image_delivery: state.config.settings.context.image_delivery.compaction,
        },
    )
    .expect("test compaction projection can be estimated");
    let CompactionProjectionFit::Fitted(projection) = projection else {
        panic!("test compaction projection is valid");
    };
    projection
}

#[test]
fn capture_rejects_duplicate_tool_call_identities() {
    let mut state = decode_checkpoint(&restorable_active_tool_checkpoint().to_string())
        .unwrap()
        .restore(config())
        .unwrap();
    let TurnState::Active(active) = &mut state.turn else {
        panic!("expected active checkpoint");
    };
    let ActivePhase::AwaitingToolBatch { batch, .. } = &mut active.phase else {
        panic!("expected tool batch checkpoint");
    };
    let mut duplicate = batch.executions[0].clone();
    duplicate.call.id = "duplicate-call".to_string();
    duplicate.call.name = "duplicate-tool".to_string();
    duplicate.state = ToolExecutionState::Completed {
        message: Message::tool_success_text(
            "duplicate-call".to_string(),
            "duplicate-tool".to_string(),
            "done",
        ),
    };
    batch.executions.push(duplicate.clone());
    batch.executions.push(duplicate);
    let mut messages = state.context.messages().to_vec();
    {
        let stored = messages.last_mut().unwrap();
        let Message::Assistant { content } = &mut stored.message else {
            panic!("expected assistant tool-call message");
        };
        for _ in 0..2 {
            content.push(AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                id: "duplicate-call".to_string(),
                name: "duplicate-tool".to_string(),
                arguments: ToolArguments::Json {
                    raw: "{}".to_string(),
                    value: json!({}),
                },
                meta: None,
            }));
        }
    }
    state.context.replace_messages(messages);

    let error = Checkpoint::capture(&state).unwrap_err();
    assert!(error.contains("duplicate tool call ID"), "{error}");
}

#[test]
fn capture_rejects_reordered_program_continuations() {
    let mut state = crate::core::testing::advance_llm(
        crate::core::testing::started(),
        crate::core::testing::assistant_tool(
            "run_typescript",
            json!({
                "code": "async function main() { return Promise.all([tools.file_system.read_file({ path: 'a.txt' }), tools.file_system.read_file({ path: 'b.txt' })]); }"
            }),
        ),
    )
    .state;
    let TurnState::Active(active) = &mut state.turn else {
        panic!("expected active checkpoint");
    };
    let ActivePhase::AwaitingToolBatch { batch, .. } = &mut active.phase else {
        panic!("expected tool batch checkpoint");
    };
    let ToolExecutionState::ProgramPending { execution } = &mut batch.executions[0].state else {
        panic!("expected pending program");
    };
    execution.test_swap_pending_operations(0, 1);

    let error = Checkpoint::capture(&state).unwrap_err();

    assert!(error.contains("does not match continuation"), "{error}");
}

/// *Prepare*: Start a real programmatic call and then corrupt its derived evaluator fields.
/// *Do*: Capture each impossible internal state.
/// *Assert*: Capture rejects fields that V1 reconstructs instead of persisting.
#[test]
fn capture_rejects_program_source_or_input_that_cannot_come_from_run_typescript() {
    let state = crate::core::testing::advance_llm(
        crate::core::testing::started(),
        crate::core::testing::assistant_tool(
            "run_typescript",
            json!({
                "code": "async function main() { return tools.file_system.read_file({ path: 'a.txt' }); }"
            }),
        ),
    )
    .state;

    let cases: [(&str, ProgramExecutionMutation); 2] = [
        ("source", |execution: &mut crate::core::ProgramExecution| {
            execution.test_replace_source("different source");
        }),
        ("input", |execution: &mut crate::core::ProgramExecution| {
            execution.test_replace_input(json!({ "unexpected": true }));
        }),
    ];
    for (label, mutate) in cases {
        let mut invalid = state.clone();
        let TurnState::Active(active) = &mut invalid.turn else {
            panic!("expected active checkpoint");
        };
        let ActivePhase::AwaitingToolBatch { batch, .. } = &mut active.phase else {
            panic!("expected tool batch checkpoint");
        };
        let ToolExecutionState::ProgramPending { execution } = &mut batch.executions[0].state
        else {
            panic!("expected pending program");
        };
        mutate(execution);

        assert!(
            Checkpoint::capture(&invalid).is_err(),
            "captured invalid program {label}"
        );
    }
}

#[test]
fn restore_rejects_noncanonical_turn_identities() {
    let mut terminal = checkpoint_with_turn(json!({
        "type": "terminal",
        "turn_id": "",
        "outcome": { "type": "rejected", "reason": "no" }
    }));
    terminal["last_finished_turn_id"] = json!("");

    let mut queued = restorable_active_tool_checkpoint();
    queued["turn"]["queued"] = json!({
        "type": "pending",
        "turn_id": "",
        "messages": [{
            "message": {
                "role": "user",
                "content": [{ "type": "text", "text": "queued" }]
            },
            "source": "history"
        }]
    });

    let mut previous_turn = checkpoint_with_turn(json!({
        "type": "active",
        "turn_id": "turn-new",
        "iterations": 0,
        "phase": {
            "type": "awaiting_hook",
            "pending": {
                "hook": "pre_agent_turn",
                "hook_binding_ids": ["binding-1"],
                "user_content": [{ "type": "text", "text": "start" }],
                "previous_turn": {
                    "type": "terminal",
                    "turn_id": "turn-previous",
                    "outcome": { "type": "rejected", "reason": "no" }
                }
            }
        },
        "queued": { "type": "empty" },
        "pending_steer": [],
        "model_input_ready": false
    }));
    previous_turn["last_finished_turn_id"] = json!("turn-other");

    for (label, value) in [
        ("empty terminal identity", terminal),
        ("empty queued identity", queued),
        ("mismatched previous turn", previous_turn),
    ] {
        let checkpoint = decode_checkpoint(&value.to_string()).unwrap();
        assert!(checkpoint.restore(config()).is_err(), "restored {label}");
    }
}

#[test]
fn restore_rejects_pending_calls_with_invalid_model_arguments() {
    let mut checkpoint = restorable_active_tool_checkpoint();
    checkpoint["context"]["messages"][1]["message"]["content"][0]["arguments"] = json!({
        "type": "invalid_json",
        "raw": "{",
        "error": "invalid arguments"
    });

    let checkpoint = decode_checkpoint(&checkpoint.to_string()).unwrap();
    let error = checkpoint.restore(config()).unwrap_err();
    assert!(
        error.contains("invalid arguments must already be completed"),
        "{error}"
    );
}

#[test]
fn restore_rejects_noncanonical_control_messages() {
    let mut queued = restorable_active_tool_checkpoint();
    queued["turn"]["queued"] = json!({
        "type": "pending",
        "turn_id": "turn-queued",
        "messages": [{
            "message": {
                "role": "user",
                "content": [{ "type": "text", "text": "queued" }]
            },
            "source": "injection"
        }]
    });

    let mut steering = restorable_active_tool_checkpoint();
    steering["turn"]["pending_steer"] = json!([{
        "message": {
            "role": "user",
            "content": [{ "type": "text", "text": "steer" }]
        },
        "source": "injection"
    }]);

    for (label, value) in [("queued turn", queued), ("pending steering", steering)] {
        let checkpoint = decode_checkpoint(&value.to_string()).unwrap();
        let error = checkpoint.restore(config()).unwrap_err();
        assert!(error.contains("visible user messages"), "{label}: {error}");
    }
}

#[test]
fn restore_rejects_direct_targets_the_model_could_not_call() {
    let mut programmatic_only = restorable_active_tool_checkpoint();
    programmatic_only["turn"]["phase"]["batch"]["executions"][0]["call"] = json!({
        "type": "runtime_builtin",
        "name": "process.start",
        "arguments": { "command": "cargo test" }
    });

    let mut empty_provided_identity = restorable_active_tool_checkpoint();
    empty_provided_identity["turn"]["phase"]["batch"]["executions"][0]["call"]["group_name"] =
        json!("");

    for (label, value, expected) in [
        (
            "programmatic-only target",
            programmatic_only,
            "programmatic-only built-in",
        ),
        (
            "empty provided identity",
            empty_provided_identity,
            "identity must not be empty",
        ),
    ] {
        let checkpoint = decode_checkpoint(&value.to_string()).unwrap();
        let error = checkpoint.restore(config()).unwrap_err();
        assert!(error.contains(expected), "{label}: {error}");
    }
}

#[test]
fn restore_accepts_a_skill_call_awaiting_a_pre_tool_hook() {
    let mut checkpoint = restorable_active_tool_checkpoint();
    checkpoint["turn"]["phase"]["batch"]["executions"][0]["call"] = json!({
        "type": "runtime_builtin",
        "name": "skill.read",
        "arguments": {}
    });
    checkpoint["context"]["messages"][1]["message"]["content"][0]["name"] = json!("skill");

    let restored = decode_checkpoint(&checkpoint.to_string())
        .unwrap()
        .restore(config());

    assert!(restored.is_ok(), "{:?}", restored.err());
}

#[test]
fn restore_rejects_success_for_invalid_model_arguments() {
    let mut checkpoint = restorable_active_tool_checkpoint();
    let execution = &mut checkpoint["turn"]["phase"]["batch"]["executions"][0];
    *execution = json!({
        "type": "completed",
        "result": {
            "type": "success",
            "content": []
        }
    });
    checkpoint["context"]["messages"][1]["message"]["content"][0]["arguments"] = json!({
        "type": "invalid_json",
        "raw": "{",
        "error": "invalid JSON"
    });

    let checkpoint = decode_checkpoint(&checkpoint.to_string()).unwrap();
    let error = checkpoint.restore(config()).unwrap_err();

    assert!(error.contains("must contain a failure message"), "{error}");
}

#[test]
fn restore_rejects_tool_batch_cardinality_that_differs_from_the_assistant_message() {
    let mut checkpoint = restorable_active_tool_checkpoint();
    checkpoint["turn"]["phase"]["batch"]["executions"]
        .as_array_mut()
        .unwrap()
        .push(json!({
            "type": "completed",
            "result": { "type": "success", "content": [] }
        }));

    let checkpoint = decode_checkpoint(&checkpoint.to_string()).unwrap();
    let error = checkpoint.restore(config()).unwrap_err();

    assert!(error.contains("does not match its assistant"), "{error}");
}

#[test]
fn decode_rejects_redundant_internal_program_function_payloads() {
    let mut checkpoint = super::cases::restorable_pending_program_checkpoint();
    let operations =
        checkpoint["turn"]["phase"]["batch"]["executions"][0]["execution"]["operations"]
            .as_array_mut()
            .unwrap();
    operations.insert(
        0,
        json!({
            "type": "internal",
            "id": "internal-1",
            "function": { "name": "Date.now", "arguments": {} },
            "outcome": { "type": "resolved", "value": 1 }
        }),
    );

    let error = decode_checkpoint(&checkpoint.to_string()).unwrap_err();

    assert!(error.contains("unknown field `function`"), "{error}");
}

#[test]
fn restore_rejects_direct_only_programmatic_targets() {
    let mut checkpoint = super::cases::restorable_pending_program_checkpoint();
    let operation =
        &mut checkpoint["turn"]["phase"]["batch"]["executions"][0]["execution"]["operations"][0];
    operation["function"] = json!({ "name": "skill", "arguments": { "name": "review" } });
    operation["execution"]["call"] = json!({
        "type": "runtime_builtin",
        "name": "skill.read",
        "arguments": { "name": "review" }
    });

    let checkpoint = decode_checkpoint(&checkpoint.to_string()).unwrap();
    let error = checkpoint.restore(config()).unwrap_err();

    assert!(error.contains("direct-only built-in"), "{error}");
}

/// *Prepare*: Pair a program continuation with malformed historical model calls.
/// *Do*: Restore each checkpoint through the public versioned decoder.
/// *Assert*: Restore rejects a wrong tool name and a missing string source.
#[test]
fn restore_rejects_program_state_without_a_valid_run_typescript_parent() {
    let mut wrong_name = super::cases::restorable_pending_program_checkpoint();
    wrong_name["context"]["messages"][1]["message"]["content"][0]["name"] = json!("read_file");

    let mut missing_code = super::cases::restorable_pending_program_checkpoint();
    missing_code["context"]["messages"][1]["message"]["content"][0]["arguments"] = json!({
        "type": "json",
        "raw": "{}",
        "value": {}
    });

    for (label, value, expected) in [
        ("wrong name", wrong_name, "must be named"),
        ("missing code", missing_code, "must contain string code"),
    ] {
        let checkpoint = decode_checkpoint(&value.to_string()).unwrap();
        let error = checkpoint.restore(config()).unwrap_err();
        assert!(error.contains(expected), "{label}: {error}");
    }
}
