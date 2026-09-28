use super::*;

fn recovery_error() -> Value {
    json!({
        "code": "action_recovery_unsafe",
        "message": "the pending Runtime Action cannot be safely recovered",
        "retryable": false,
        "details": {
            "action_kind": "subagent.spawn",
        },
    })
}

fn fail_turn(turn_id: &str, action: &Value, error: Value) -> Value {
    json!({
        "type": "fail_turn",
        "expected_turn_id": turn_id,
        "action_id": action_id(action),
        "error": error,
    })
}

fn turn_failed(turn_id: &str, error: Value) -> Value {
    json!({
        "type": "turn_failed",
        "turn_id": turn_id,
        "error": error,
    })
}

///
/// *Prepare*: A checkpoint contains two pending Runtime Actions from one active turn.
/// *Do*: Restore it, reject a wrong turn and a stale Action, then fail the turn for one exact Action.
/// *Assert*: Rejections consume no input, the failure abandons both Actions with the Runtime-failure cause, records the supplied error, and schedules no work.
///
#[test]
fn unsafe_recovery_fails_the_expected_active_turn_after_restore() {
    // Prepare
    let turn_id = "turn-unsafe-recovery";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "delegate two unsafe operations");
    let source = "async function main() { return Promise.all([tools.agent.spawn({ agentName: 'alpha', message: 'Investigate alpha' }), tools.agent.spawn({ agentName: 'beta', message: 'Investigate beta' })]); }";
    let actions = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            "program-unsafe-recovery",
            source,
            [
                runtime_tool(
                    "subagent.spawn",
                    json!({"agentName": "alpha", "message": "Investigate alpha"}),
                ),
                runtime_tool(
                    "subagent.spawn",
                    json!({"agentName": "beta", "message": "Investigate beta"}),
                ),
            ],
        )
        .actions();
    runtime.restart_from_checkpoint();
    let error = recovery_error();

    // Do
    runtime.reject(
        fail_turn("turn-other", &actions[0], error.clone()),
        json!({
            "code": "invalid_command",
            "error": {
                "code": "invalid_command",
                "message": "fail_turn turn mismatch: expected \"turn-unsafe-recovery\", got \"turn-other\"",
                "retryable": false,
                "details": null,
            },
        }),
    );
    let mut stale = fail_turn(turn_id, &actions[0], error.clone());
    stale["action_id"] = json!("00000000-0000-0000-0000-000000000000");
    runtime.reject(
        stale,
        json!({
            "code": "invalid_correlation",
            "received_action_id": "00000000-0000-0000-0000-000000000000",
            "pending_action_ids": [action_id(&actions[0]), action_id(&actions[1])],
        }),
    );
    runtime.apply(
        fail_turn(turn_id, &actions[0], error.clone()),
        failed(turn_id, error.clone())
            .observe(turn_failed(turn_id, error))
            .observe(action_abandoned(turn_id, &actions[0], "runtime_failure"))
            .observe(action_abandoned(turn_id, &actions[1], "runtime_failure")),
    );

    // Assert
    runtime.reject(
        fail_turn(turn_id, &actions[0], recovery_error()),
        json!({
            "code": "invalid_correlation",
            "received_action_id": action_id(&actions[0]),
            "pending_action_ids": [],
        }),
    );
}
