use super::*;

struct FileSystemCase {
    direct_name: &'static str,
    runtime_name: &'static str,
    model_arguments: Value,
    runtime_arguments: Value,
    result: Value,
}

fn filesystem_cases() -> Vec<FileSystemCase> {
    vec![
        FileSystemCase {
            direct_name: "read_file",
            runtime_name: "file_system.read_file",
            model_arguments: json!({"path": "input.txt", "offset": 1, "limit": 2}),
            runtime_arguments: json!({"path": "input.txt", "offset": 1, "limit": 2}),
            result: json!({
                "path": "input.txt",
                "content": "content",
                "file_size_bytes": 7,
                "returned_bytes": 7,
                "offset": 1,
                "lines_read": 1,
                "was_truncated": false,
            }),
        },
        FileSystemCase {
            direct_name: "write_file",
            runtime_name: "file_system.write_file",
            model_arguments: json!({"path": "output.txt", "content": "content"}),
            runtime_arguments: json!({"path": "output.txt", "content": "content"}),
            result: json!({
                "path": "output.txt",
                "bytes_written": 7,
                "file_existed": true,
            }),
        },
        FileSystemCase {
            direct_name: "edit",
            runtime_name: "file_system.search_replace",
            model_arguments: json!({
                "file_path": "source.txt",
                "old_string": "before",
                "new_string": "after",
                "replace_all": false,
            }),
            runtime_arguments: json!({
                "file_path": "source.txt",
                "content": [{
                    "old_str": "before",
                    "new_str": "after",
                    "replace_all": false,
                }],
            }),
            result: json!({
                "file": "source.txt",
                "lines_changed": 1,
                "warnings": [],
            }),
        },
        FileSystemCase {
            direct_name: "bash",
            runtime_name: "file_system.bash",
            model_arguments: json!({"command": "pwd", "timeout_seconds": 5}),
            runtime_arguments: json!({"command": "pwd", "timeout_seconds": 5}),
            result: json!({
                "command": "pwd",
                "stdout": "/workspace\n",
                "stderr": "",
                "returncode": 0,
                "was_truncated": false,
            }),
        },
    ]
}

fn direct_tool_completion(action_id: &str, call_id: &str, name: &str, arguments: &Value) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id,
        "result": {
            "parts": [{
                "type": "tool_call",
                "id": call_id,
                "name": name,
                "arguments_json": arguments.to_string(),
            }],
            "finish_reason": "tool_call",
            "usage": null,
        },
    })
}

///
/// *Prepare*: Each built-in filesystem tool starts from a fresh session and receives valid arguments and structured output.
/// *Do*: Invoke every tool through its bare top-level name, then return its Runtime result.
/// *Assert*: Every serialized action uses the stable qualified Runtime route and translated arguments, accepts its output schema, and resumes model execution.
///
#[test]
fn direct_filesystem_route_matrix_is_step_visible() {
    // Prepare
    let cases = filesystem_cases();

    for (index, case) in cases.into_iter().enumerate() {
        let mut session = HarnessSession::create(config()).expect("session creates");
        let turn_id = format!("turn-direct-filesystem-{index}");
        let started = accepted_value(session.apply(input(
            1,
            user_message(&turn_id, "use the filesystem", "queue"),
        )));
        let completion_action_id = dispatched_action_id(&started).to_string();
        let call_id = format!("call-direct-filesystem-{index}");

        // Do
        let pending = accepted_value(session.apply(input(
            2,
            direct_tool_completion(
                &completion_action_id,
                &call_id,
                case.direct_name,
                &case.model_arguments,
            ),
        )));
        let action = &pending["transition"]["next"]["directives"][0]["action"];
        let resumed = accepted_value(session.apply(input(
            3,
            json!({
                "type": "tool_succeeded",
                "action_id": action["action_id"],
                "call_id": action["call_id"],
                "result": {
                    "type": "success",
                    "content": [],
                    "structured_content": case.result,
                },
            }),
        )));

        // Assert
        assert_eq!(action["type"], "runtime_builtin_tool_call");
        assert_eq!(action["call_id"], call_id);
        assert_eq!(action["call"]["type"], "runtime_builtin");
        assert_eq!(action["call"]["name"], case.runtime_name);
        assert_eq!(action["call"]["arguments"], case.runtime_arguments);
        assert_eq!(
            resumed["transition"]["observations"][0]["type"],
            "tool_result_committed"
        );
        assert_eq!(
            resumed["transition"]["next"]["directives"][0]["action"]["type"],
            "llm_call"
        );
    }
}

///
/// *Prepare*: One `run_typescript` call starts all four filesystem operations in parallel.
/// *Do*: Serialize the dispatched actions, checkpoint the pending program, and restore it into a new session.
/// *Assert*: Programmatic names map to the same four qualified Runtime routes with translated arguments, and restoration preserves every pending route.
///
#[test]
fn programmatic_filesystem_route_matrix_survives_restore() {
    // Prepare
    let config = config();
    let mut session = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message(
            "turn-programmatic-filesystem",
            "use every filesystem tool",
            "queue",
        ),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source = "async function main() { return Promise.all([tools.file_system.read_file({ path: 'input.txt', offset: 1, limit: 2 }), tools.file_system.write_file({ path: 'output.txt', content: 'content' }), tools.file_system.edit({ file_path: 'source.txt', old_string: 'before', new_string: 'after', replace_all: false }), tools.file_system.bash({ command: 'pwd', timeout_seconds: 5 })]); }";

    // Do
    let pending = accepted_value(session.apply(input(
        2,
        direct_tool_completion(
            &completion_action_id,
            "call-programmatic-filesystem",
            "run_typescript",
            &json!({"code": source}),
        ),
    )));
    let actions = pending["transition"]["next"]["directives"]
        .as_array()
        .expect("program dispatches filesystem actions")
        .iter()
        .map(|directive| directive["action"].clone())
        .collect::<Vec<_>>();
    let checkpoint = session.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");

    // Assert
    let expected = filesystem_cases();
    assert_eq!(actions.len(), expected.len());
    for (action, case) in actions.iter().zip(&expected) {
        assert_eq!(action["type"], "runtime_builtin_tool_call");
        assert_eq!(action["call"]["name"], case.runtime_name);
        assert_eq!(action["call"]["arguments"], case.runtime_arguments);
    }
    assert_eq!(
        inspection_value(&restored)["pending_actions"],
        inspection_value(&session)["pending_actions"]
    );
    assert_eq!(checkpoint_value(&restored), checkpoint_value(&session));
}
