use super::*;
use crate::core::testing::typescript_execution_id;

fn runtime_with_default_permission(default_decision: &str) -> SynchronousRuntime {
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["model_wait"] = json!({"mode": "enabled"});
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [],
        "default": default_decision,
    });
    SynchronousRuntime::new(serde_json::from_value(config_value).expect("policy is valid"))
}

/// *Prepare*: A Session policy asks for, or denies, every tool call by default.
/// *Do*: The model calls `wait` directly.
/// *Assert*: The Runtime receives the wait at once, with no approval and no denial.
#[test]
fn direct_wait_bypasses_ask_and_deny_policies() {
    for default_decision in ["ask", "deny"] {
        let turn_id = "orchestration-wait";
        let mut runtime = runtime_with_default_permission(default_decision);
        let completion = runtime.start_turn(turn_id, "wait for the child");
        let calls = vec![tool_call("wait-1", "wait", json!({"timeout_ms": 30_000}))];

        runtime
            .apply_at(
                tool_call_completion(action_id(&completion), calls.clone()),
                1_700_000_000_000,
                running(turn_id)
                    .observe(assistant_tool_calls_committed(turn_id, &completion, &calls))
                    .dispatch(runtime_tool(
                        "self.wait",
                        json!({"timeout_ms": 30_000, "deadline_unix_ms": 1_700_000_030_000_u64}),
                    ))
                    .observe(tool_execution_started(turn_id, "wait-1")),
            )
            .only_action();
    }
}

/// *Prepare*: A Session policy asks for every tool call by default.
/// *Do*: A `run_typescript` program sleeps, lists agents, messages a child, and interrupts it.
/// *Assert*: The Runtime receives each orchestration call without an approval.
#[test]
fn programmatic_orchestration_bypasses_ask_policy() {
    let cases = [
        (
            "tools.self.sleep({ seconds: 1 })",
            runtime_tool("self.sleep", json!({"seconds": 1})),
        ),
        (
            "tools.agents.list({})",
            runtime_tool("agents.list", json!({})),
        ),
        (
            "tools.agents.send_message({ agent_path: 'alpha', message: 'Check logs' })",
            runtime_tool(
                "agents.send_message",
                json!({"agent_path": "alpha", "message": "Check logs"}),
            ),
        ),
        (
            "tools.agents.interrupt({ name: 'alpha' })",
            runtime_tool("agents.interrupt", json!({"name": "alpha"})),
        ),
    ];
    for (call, expected) in cases {
        let turn_id = "orchestration-program";
        let mut runtime = runtime_with_default_permission("ask");
        let completion = runtime.start_turn(turn_id, "coordinate the children");
        let source = format!("async function main() {{ return {call}; }}");

        runtime
            .complete_with_typescript_program(turn_id, &completion, "program", &source, [expected])
            .only_action();
    }
}

/// *Prepare*: A Session policy asks for every tool call by default.
/// *Do*: A `run_typescript` program starts a child agent.
/// *Assert*: The spawn still opens an approval before the Runtime starts the child.
#[test]
fn programmatic_spawn_still_asks_for_approval() {
    let turn_id = "orchestration-spawn";
    let call_id = "program";
    let mut runtime = runtime_with_default_permission("ask");
    let completion = runtime.start_turn(turn_id, "delegate");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));

    let approval = runtime
        .complete_with_typescript_program(
            turn_id,
            &completion,
            call_id,
            "async function main() { return tools.agents.spawn({ name: 'child', message: 'work' }); }",
            [expected_approval(&operation_id)],
        )
        .only_action();

    runtime
        .apply(
            approve(&approval),
            running(turn_id).dispatch(runtime_tool(
                "agents.spawn",
                json!({"name": "child", "message": "work"}),
            )),
        )
        .only_action();
}
