use super::*;

struct ProcessCase {
    runtime_name: &'static str,
    arguments: Value,
    result: Value,
}

fn process_cases() -> Vec<ProcessCase> {
    vec![
        ProcessCase {
            runtime_name: "process.start",
            arguments: json!({
                "command": "sleep 10",
                "cwd": "/workspace",
                "env": {"MODE": "test"},
            }),
            result: json!({"processId": "process-1", "status": "running"}),
        },
        ProcessCase {
            runtime_name: "process.output",
            arguments: json!({
                "processId": "process-1",
                "from": "start",
                "cursor": 0,
                "waitMs": 100,
                "maxBytes": 16000,
            }),
            result: json!({
                "processId": "process-1",
                "status": "running",
                "exitCode": null,
                "output": "ready",
                "outputStartCursor": 0,
                "nextCursor": 5,
                "bytesAvailable": 5,
                "hasMore": false,
                "truncatedBefore": false,
            }),
        },
        ProcessCase {
            runtime_name: "process.output",
            arguments: json!({
                "processId": "process-1",
                "from": "end",
                "maxBytes": 1024,
            }),
            result: json!({
                "processId": "process-1",
                "status": "completed",
                "exitCode": 0,
                "output": "done",
                "outputStartCursor": 0,
                "nextCursor": 9,
                "bytesAvailable": 9,
                "hasMore": false,
                "truncatedBefore": false,
            }),
        },
        ProcessCase {
            runtime_name: "process.write",
            arguments: json!({"processId": "process-1", "text": "hello"}),
            result: json!({
                "processId": "process-1",
                "status": "running",
                "bytesWritten": 5,
            }),
        },
        ProcessCase {
            runtime_name: "process.write",
            arguments: json!({
                "processId": "process-1",
                "control": ["ctrl_c", "enter"],
            }),
            result: json!({
                "processId": "process-1",
                "status": "running",
                "bytesWritten": 2,
            }),
        },
        ProcessCase {
            runtime_name: "process.write",
            arguments: json!({
                "processId": "process-1",
                "bytesBase64": "aGVsbG8=",
            }),
            result: json!({
                "processId": "process-1",
                "status": "running",
                "bytesWritten": 5,
            }),
        },
        ProcessCase {
            runtime_name: "process.list",
            arguments: json!({}),
            result: json!({
                "processes": [{
                    "processId": "process-1",
                    "command": "sleep 10",
                    "status": "running",
                    "exitCode": null,
                    "outputPath": null,
                }]
            }),
        },
        ProcessCase {
            runtime_name: "process.stop",
            arguments: json!({"processId": "process-1"}),
            result: json!({
                "processId": "process-1",
                "status": "stopped",
                "exitCode": null,
            }),
        },
    ]
}

fn config_with_background_processes(mode: &str) -> HarnessConfig {
    let mut value = serde_json::to_value(config()).expect("base config serializes");
    value["settings"]["tools"]["background_processes"] = json!({"mode": mode});
    serde_json::from_value(value).expect("background-process config deserializes")
}

fn run_typescript_completion(action_id: &str, call_id: &str, source: &str) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id,
        "result": {
            "parts": [{
                "type": "tool_call",
                "id": call_id,
                "name": "run_typescript",
                "arguments_json": json!({"code": source}).to_string(),
            }],
            "finish_reason": "tool_call",
            "usage": null,
        },
    })
}

fn structured_tool_success(action: &Value, structured_content: &Value) -> Value {
    json!({
        "type": "tool_succeeded",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": {
            "type": "success",
            "content": [],
            "structured_content": structured_content,
        },
    })
}

fn serialized_initial_messages(session: &mut HarnessSession, turn_id: &str) -> (Value, String) {
    let started = accepted_value(session.apply(input(
        1,
        user_message(turn_id, "manage a background command", "queue"),
    )));
    let messages = started["transition"]["next"]["directives"][0]["action"]["model_input"]
        ["messages"]["messages"]
        .clone();
    let serialized = serde_json::to_string(&messages).expect("initial messages serialize");
    (started, serialized)
}

///
/// *Prepare*: One `run_typescript` call requests every background-process operation while the feature is enabled.
/// *Do*: Serialize all five Runtime actions, checkpoint the pending program, restore it, and return output matching every declared schema.
/// *Assert*: Programmatic names map to stable Runtime routes, restoration preserves them, and both sessions resume identically.
///
#[test]
fn programmatic_background_process_route_matrix_survives_restore() {
    // Prepare
    let config = config_with_background_processes("enabled");
    let mut session = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message(
            "turn-programmatic-processes",
            "use every background-process operation",
            "queue",
        ),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source = "async function main() { return Promise.all([tools.process.start({ command: 'sleep 10', cwd: '/workspace', env: { MODE: 'test' } }), tools.process.output({ processId: 'process-1', from: 'start', cursor: 0, waitMs: 100, maxBytes: 16000 }), tools.process.output({ processId: 'process-1', from: 'end', maxBytes: 1024 }), tools.process.write({ processId: 'process-1', text: 'hello' }), tools.process.write({ processId: 'process-1', control: ['ctrl_c', 'enter'] }), tools.process.write({ processId: 'process-1', bytesBase64: 'aGVsbG8=' }), tools.process.list({}), tools.process.stop({ processId: 'process-1' })]); }";

    // Do
    let pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(&completion_action_id, "call-processes", source),
    )));
    let actions = pending["transition"]["next"]["directives"]
        .as_array()
        .expect("program dispatches background-process actions")
        .iter()
        .map(|directive| directive["action"].clone())
        .collect::<Vec<_>>();
    let checkpoint = session.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");

    // Assert
    let expected = process_cases();
    assert_eq!(actions.len(), expected.len());
    for (action, case) in actions.iter().zip(&expected) {
        assert_eq!(action["type"], "runtime_builtin_tool_call");
        assert_eq!(action["call"]["name"], case.runtime_name);
        assert_eq!(action["call"]["arguments"], case.arguments);
    }
    assert_eq!(
        inspection_value(&restored)["pending_actions"],
        inspection_value(&session)["pending_actions"]
    );
    assert_eq!(checkpoint_value(&restored), checkpoint_value(&session));

    for (index, (action, case)) in actions.iter().zip(&expected).enumerate() {
        let command = structured_tool_success(action, &case.result);
        let live = accepted_value(session.apply(input(3 + index as u64, command.clone())));
        let restored_result = accepted_value(restored.apply(input(1 + index as u64, command)));

        assert_eq!(
            live["transition"]["observations"],
            restored_result["transition"]["observations"]
        );
        assert_eq!(
            live["transition"]["observations"][0]["type"],
            "tool_result_committed"
        );
        assert_eq!(checkpoint_value(&restored), checkpoint_value(&session));

        if index + 1 == expected.len() {
            assert_eq!(
                live["transition"]["next"]["directives"][0]["action"]["type"],
                "llm_call"
            );
            assert_eq!(
                restored_result["transition"]["next"]["directives"][0]["action"]["type"],
                "llm_call"
            );
        }
    }
}

///
/// *Prepare*: A session starts with disabled background processes and complete settings encoded through the Step configuration JSON format.
/// *Do*: Complete one turn, reconfigure background processes to enabled, start another turn, and call `tools.process.start`.
/// *Assert*: The tagged settings stay exact, prompt replacement exposes the feature, and reconfiguration makes the Runtime route callable.
///
#[test]
fn background_process_settings_reconfigure_prompt_and_routes() {
    // Prepare
    let disabled_config = config_with_background_processes("disabled");
    let disabled_settings =
        serde_json::to_value(&disabled_config.settings).expect("disabled settings serialize");
    let mut enabled_settings = disabled_settings.clone();
    enabled_settings["tools"]["background_processes"] = json!({"mode": "enabled"});
    let mut session = HarnessSession::create(disabled_config).expect("disabled session creates");
    let (disabled_started, disabled_messages) =
        serialized_initial_messages(&mut session, "turn-processes-disabled");
    let disabled_action_id = dispatched_action_id(&disabled_started).to_string();
    accepted_value(session.apply(input(
        2,
        completion(
            &disabled_action_id,
            json!([{"type": "text", "text": "disabled done"}]),
        ),
    )));

    // Do
    let reconfigured = accepted_value(session.apply(input(
        3,
        json!({
            "type": "reconfigure",
            "changes": [{"type": "settings", "value": enabled_settings.clone()}],
        }),
    )));
    let enabled_started = accepted_value(session.apply(input(
        4,
        user_message(
            "turn-processes-enabled",
            "start a background command",
            "queue",
        ),
    )));
    let enabled_action = &enabled_started["transition"]["next"]["directives"][0]["action"];
    let enabled_action_id = enabled_action["action_id"]
        .as_str()
        .expect("enabled turn dispatches a completion")
        .to_string();
    let attempted = accepted_value(session.apply(input(
        5,
        run_typescript_completion(
            &enabled_action_id,
            "call-enabled-process",
            "async function main() { return tools.process.start({ command: 'sleep 10' }); }",
        ),
    )));

    // Assert
    assert_eq!(
        disabled_settings["tools"]["background_processes"],
        json!({"mode": "disabled"})
    );
    assert_eq!(
        enabled_settings["tools"]["background_processes"],
        json!({"mode": "enabled"})
    );
    assert!(!disabled_messages.contains("## Background processes"));
    assert_eq!(reconfigured["transition"]["turn"]["status"], "completed");
    assert_eq!(enabled_action["model_input"]["messages"]["type"], "replace");
    let enabled_messages =
        serde_json::to_string(&enabled_action["model_input"]["messages"]["messages"])
            .expect("enabled messages serialize");
    assert!(enabled_messages.contains("## Background processes"));
    assert!(enabled_messages.contains("tools.process.output"));
    let process_action = &attempted["transition"]["next"]["directives"][0]["action"];
    assert_eq!(process_action["type"], "runtime_builtin_tool_call");
    assert_eq!(process_action["call"]["name"], "process.start");
    assert_eq!(
        process_action["call"]["arguments"],
        json!({"command": "sleep 10"})
    );
}

///
/// *Prepare*: A pre-tool hook selects only the canonical `process.start` identity in an enabled session.
/// *Do*: Start a programmatic process call, checkpoint the hook, restore it, and complete the hook with rewritten arguments.
/// *Assert*: Core selects the configured binding and both sessions dispatch the same hook-approved Runtime action.
///
#[test]
fn background_process_hooks_use_the_canonical_runtime_identity() {
    // Prepare
    let mut config = config_with_background_processes("enabled");
    config.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": "process-start-pre",
        "point": "pre_tool_call",
        "order": 0,
        "selector": {
            "type": "tool_keys",
            "tool_keys": [{
                "target": "process",
                "qualified_name": "process.start",
            }]
        }
    }]))
    .expect("process hook configuration is valid");
    let mut session = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-process-hook", "start a command", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let hook_pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(
            &completion_action_id,
            "call-process-hook",
            "async function main() { return tools.process.start({ command: 'sleep 10' }); }",
        ),
    )));
    let hook_action = &hook_pending["transition"]["next"]["directives"][0]["action"];
    let hook_action_id = hook_action["action_id"]
        .as_str()
        .expect("hook action has an ID")
        .to_string();
    let checkpoint = session.checkpoint().expect("hook checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("hook checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("hook checkpoint decodes");
    let mut restored =
        HarnessSession::restore(config, decoded, 0).expect("hook checkpoint restores");
    let hook_result = json!({
        "type": "hook_completed",
        "action_id": hook_action_id,
        "result": {
            "hook": "pre_tool_call",
            "output": {
                "type": "continue",
                "effective_arguments": {
                    "command": "sleep 20",
                    "cwd": "/workspace",
                },
            },
        },
    });

    // Do
    let tool_pending = accepted_value(session.apply(input(3, hook_result.clone())));
    let restored_tool_pending = accepted_value(restored.apply(input(1, hook_result)));

    // Assert
    assert_eq!(hook_action["type"], "hook_call");
    assert_eq!(
        hook_action["hook_binding_ids"],
        json!(["process-start-pre"])
    );
    assert_eq!(
        hook_action["input"]["tool_call"]["call"]["name"],
        "process.start"
    );
    let tool_action = &tool_pending["transition"]["next"]["directives"][0]["action"];
    let restored_tool_action =
        &restored_tool_pending["transition"]["next"]["directives"][0]["action"];
    assert_eq!(restored_tool_action, tool_action);
    assert_eq!(tool_action["type"], "runtime_builtin_tool_call");
    assert_eq!(tool_action["call"]["name"], "process.start");
    assert_eq!(
        tool_action["call"]["arguments"],
        json!({"command": "sleep 20", "cwd": "/workspace"})
    );
}
