use super::*;

fn run_typescript_completion(action_id: &str, calls: &[(&str, &str)]) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id,
        "result": {
            "parts": calls
                .iter()
                .map(|(call_id, source)| json!({
                    "type": "tool_call",
                    "id": call_id,
                    "name": "run_typescript",
                    "arguments_json": json!({"code": source}).to_string(),
                }))
                .collect::<Vec<_>>(),
            "finish_reason": "tool_call",
            "usage": null,
        },
    })
}

fn programmatic_read_result(action: &Value, content: &str) -> Value {
    let path = action["call"]["arguments"]["path"]
        .as_str()
        .expect("filesystem action has a path");
    json!({
        "type": "tool_succeeded",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": {
            "type": "success",
            "content": [],
            "structured_content": {
                "path": path,
                "content": content,
                "file_size_bytes": content.len(),
                "returned_bytes": content.len(),
                "offset": 0,
                "lines_read": 1,
                "was_truncated": false,
            }
        }
    })
}

fn action_without_model_input(action: &Value) -> Value {
    let mut semantic_action = action.clone();
    semantic_action
        .as_object_mut()
        .expect("action is an object")
        .remove("model_input");
    semantic_action
}

fn contains_text(messages: &[Value], expected: &str) -> bool {
    messages.iter().any(|message| {
        message["content"]
            .as_array()
            .is_some_and(|content| content.iter().any(|block| block["text"] == expected))
    })
}

fn next_llm_call_directive(result: &Value) -> &Value {
    result["transition"]["next"]["directives"]
        .as_array()
        .expect("next directives are an array")
        .iter()
        .find(|directive| directive["action"]["type"] == "llm_call")
        .expect("transition contains an llm_call directive")
}

fn next_model_messages(result: &Value) -> &[Value] {
    next_llm_call_directive(result)["action"]["model_input"]["messages"]["messages"]
        .as_array()
        .expect("llm_call carries model messages")
}

fn parent_tool_message<'a>(result: &'a Value, tool_call_id: &str) -> &'a Value {
    next_model_messages(result)
        .iter()
        .find(|message| message["role"] == "tool" && message["tool_call_id"] == tool_call_id)
        .expect("llm_call carries the parent program result")
}

///
/// *Prepare*: A fresh session exposes its initial model tool catalog.
/// *Do*: Call `tools.self.sleep` from `run_typescript`, then return its Runtime result.
/// *Assert*: Sleep is absent from the direct catalog, dispatches as `self.sleep`, and resumes model execution.
///
#[test]
fn self_sleep_is_programmatic_only_and_uses_the_runtime_route() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-self-sleep", "wait briefly", "queue"),
    )));
    let completion_action = &started["transition"]["next"]["directives"][0]["action"];
    let completion_action_id = completion_action["action_id"]
        .as_str()
        .expect("turn dispatches a completion")
        .to_string();
    let top_level_tools = completion_action["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .expect("initial tool catalog is an array");

    // Do
    let pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(
            &completion_action_id,
            &[(
                "program-self-sleep",
                "async function main() { return tools.self.sleep({ seconds: 0.25 }); }",
            )],
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
                "structured_content": {"seconds": 0.25},
            },
        }),
    )));

    // Assert
    assert!(
        top_level_tools
            .iter()
            .all(|tool| tool["name"] != "sleep" && tool["name"] != "self.sleep")
    );
    assert_eq!(action["type"], "runtime_builtin_tool_call");
    assert_eq!(action["call"]["name"], "self.sleep");
    assert_eq!(action["call"]["arguments"], json!({"seconds": 0.25}));
    assert_eq!(
        resumed["transition"]["next"]["directives"][0]["action"]["type"],
        "llm_call"
    );
}

///
/// *Prepare*: A programmatic filesystem read is stopped by a pre-tool hook before any Runtime tool action is dispatched.
/// *Do*: Complete the hook with a non-empty skip reason through the serialized Core interface.
/// *Assert*: Core commits one skipped external result, resumes the private program, and asks the model to continue without dispatching the filesystem call.
///
#[test]
fn programmatic_pre_tool_skip_commits_the_normalized_result() {
    // Prepare
    let mut config = config();
    crate::core::testing::add_always_hook(&mut config, HookPoint::PreToolCall);
    let mut session = HarnessSession::create(config).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-program-pre-skip", "read the protected file", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source =
        "async function main() { return tools.file_system.read_file({ path: 'protected.txt' }); }";
    let hook_pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(&completion_action_id, &[("program-pre-skip", source)]),
    )));
    let hook_action = &hook_pending["transition"]["next"]["directives"][0]["action"];
    let hook_action_id = hook_action["action_id"]
        .as_str()
        .expect("pre-tool hook has an action ID")
        .to_string();
    let tool_action_id = hook_action["input"]["tool_call"]["action_id"].clone();
    let tool_call_id = hook_action["input"]["tool_call"]["call_id"].clone();

    // Do
    let skipped = accepted_value(session.apply(input(
        3,
        json!({
            "type": "hook_completed",
            "action_id": hook_action_id,
            "result": {
                "hook": "pre_tool_call",
                "output": {
                    "type": "skip",
                    "reason": [{"type": "text", "text": "workspace policy denied the read"}],
                },
            },
        }),
    )));

    // Assert
    assert_eq!(hook_action["hook"], "pre_tool_call");
    let observations = skipped["transition"]["observations"]
        .as_array()
        .expect("observations are an array");
    assert_eq!(observations.len(), 2);
    assert_eq!(observations[0]["type"], "tool_result_committed");
    assert_eq!(observations[0]["action_id"], tool_action_id);
    assert_eq!(observations[0]["call_id"], tool_call_id);
    assert_eq!(observations[0]["result"]["type"], "failure");
    assert_eq!(observations[0]["result"]["error"]["code"], "tool_skipped");
    assert_eq!(
        observations[0]["result"]["content"],
        json!([{"type": "text", "text": "workspace policy denied the read"}])
    );
    assert_eq!(observations[1]["type"], "tool_execution_finished");
    assert_eq!(observations[1]["call_id"], "program-pre-skip");
    assert_eq!(observations[1]["result"]["type"], "failure");
    assert_eq!(observations[1]["result"]["error"]["code"], "tool_skipped");
    let directives = skipped["transition"]["next"]["directives"]
        .as_array()
        .expect("next directives are an array");
    assert!(
        directives
            .iter()
            .any(|directive| directive["action"]["type"] == "llm_call")
    );
    assert!(
        directives
            .iter()
            .all(|directive| directive["action"]["type"] != "runtime_builtin_tool_call")
    );
    assert_eq!(
        parent_tool_message(&skipped, "program-pre-skip")["outcome"],
        "failure"
    );
}

///
/// *Prepare*: A programmatic provided tool is checkpointed while its pre-tool hook is pending.
/// *Do*: Restore the session and continue the hook with arguments that violate its configured schema.
/// *Assert*: The restored catalog rejects the rewrite exactly and preserves both pending state and checkpoint semantics.
///
#[test]
fn restored_programmatic_provided_tool_uses_its_resolved_input_schema() {
    // Prepare
    let mut config = config();
    crate::core::testing::add_always_hook(&mut config, HookPoint::PreToolCall);
    config.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "calendar",
        "tools": [{
            "name": "find_event",
            "description": "Find a calendar event.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "exposure": "programmatic",
        }],
    }]))
    .expect("provided tool configuration is valid");
    let mut session = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message(
            "turn-restored-provided-hook",
            "find the planning event",
            "queue",
        ),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source =
        "async function main() { return tools.calendar.find_event({ query: 'planning' }); }";
    let hook_pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(
            &completion_action_id,
            &[("program-restored-provided-hook", source)],
        ),
    )));
    let hook_action_id = dispatched_action_id(&hook_pending).to_string();
    let checkpoint = session
        .checkpoint()
        .expect("pending provided-tool hook checkpoints");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored =
        HarnessSession::restore(config, decoded, 0).expect("provided-tool hook restores");
    let before_inspection = inspection_value(&restored);
    let before_checkpoint = checkpoint_value(&restored);

    // Do
    let rejected = serde_json::to_value(restored.apply(input(
        1,
        json!({
            "type": "hook_completed",
            "action_id": hook_action_id,
            "result": {
                "hook": "pre_tool_call",
                "output": {
                    "type": "continue",
                    "effective_arguments": [],
                },
            },
        }),
    )))
    .expect("rejection serializes");

    // Assert
    assert_eq!(
        rejected["rejection"]["error"]["message"],
        "pre-tool hook arguments do not satisfy the original tool schema: [] is not of type \"object\""
    );
    assert_eq!(inspection_value(&restored), before_inspection);
    assert_eq!(checkpoint_value(&restored), before_checkpoint);
}

///
/// *Prepare*: A programmatic filesystem result is waiting for a post-tool hook, so the raw result is not yet committed.
/// *Do*: Fail the pending hook through the serialized Core interface.
/// *Assert*: Core commits the hook failure for the external operation, resumes the private program with that failure, and never commits the raw result.
///
#[test]
fn programmatic_post_tool_failure_commits_only_the_hook_failure() {
    // Prepare
    let mut config = config();
    crate::core::testing::add_always_hook(&mut config, HookPoint::PostToolCall);
    let mut session = HarnessSession::create(config).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-program-post-failure", "read the report", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source =
        "async function main() { return tools.file_system.read_file({ path: 'report.txt' }); }";
    let tool_pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(&completion_action_id, &[("program-post-failure", source)]),
    )));
    let tool_action = tool_pending["transition"]["next"]["directives"][0]["action"].clone();
    let hook_pending = accepted_value(session.apply(input(
        3,
        programmatic_read_result(&tool_action, "raw report"),
    )));
    let hook_action = &hook_pending["transition"]["next"]["directives"][0]["action"];
    let hook_action_id = hook_action["action_id"]
        .as_str()
        .expect("post-tool hook has an action ID")
        .to_string();

    // Do
    let failed = accepted_value(session.apply(input(
        4,
        json!({
            "type": "hook_failed",
            "action_id": hook_action_id,
            "error": {
                "code": "hook_timeout",
                "message": "post-tool policy timed out",
                "retryable": false,
                "details": {"hook": "post_tool_call"},
            },
        }),
    )));

    // Assert
    assert_eq!(hook_pending["transition"]["observations"], json!([]));
    assert_eq!(hook_action["hook"], "post_tool_call");
    let observations = failed["transition"]["observations"]
        .as_array()
        .expect("observations are an array");
    assert_eq!(observations.len(), 2);
    assert_eq!(observations[0]["type"], "tool_result_committed");
    assert_eq!(observations[0]["action_id"], tool_action["action_id"]);
    assert_eq!(observations[0]["call_id"], tool_action["call_id"]);
    assert_eq!(observations[0]["result"]["type"], "failure");
    assert_eq!(observations[0]["result"]["error"]["code"], "hook_timeout");
    assert_eq!(
        observations[0]["result"]["content"],
        json!([{"type": "text", "text": "post-tool policy timed out"}])
    );
    assert_eq!(observations[1]["type"], "tool_execution_finished");
    assert_eq!(observations[1]["call_id"], "program-post-failure");
    assert_eq!(observations[1]["result"]["type"], "failure");
    assert_eq!(observations[1]["result"]["error"]["code"], "hook_timeout");
    assert_eq!(
        failed["transition"]["next"]["directives"][0]["action"]["type"],
        "llm_call"
    );
    assert_eq!(
        parent_tool_message(&failed, "program-post-failure")["outcome"],
        "failure"
    );
}

///
/// *Prepare*: A turn's `run_typescript` completion dispatches two parallel filesystem reads, which remain pending at an opaque checkpoint.
/// *Do*: Restore the checkpoint and return the second read before the first to both the live and restored sessions.
/// *Assert*: Only ordinary filesystem effects are observable, both continuations and checkpoints agree, and the completed program result reaches model input despite append-versus-replace delivery.
///
#[test]
fn parallel_programmatic_tools_survive_checkpoint_restore() {
    // Prepare
    let config = config();
    let mut uninterrupted = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(uninterrupted.apply(input(
        1,
        user_message("turn-program", "read both files", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source = "async function main() { const [first, second] = await Promise.all([tools.file_system.read_file({ path: 'first.txt' }), tools.file_system.read_file({ path: 'second.txt' })]); return { first: first.content, second: second.content }; }";
    let program_started = accepted_value(uninterrupted.apply(input(
        2,
        json!({
            "type": "completion_succeeded",
            "action_id": completion_action_id,
            "result": {
                "parts": [{
                    "type": "tool_call",
                    "id": "program-call",
                    "name": "run_typescript",
                    "arguments_json": json!({"code": source}).to_string(),
                }],
                "finish_reason": "tool_call",
                "usage": null,
            }
        }),
    )));
    let tool_actions: [Value; 2] = program_started["transition"]["next"]["directives"]
        .as_array()
        .expect("parallel reads produce action directives")
        .iter()
        .map(|directive| directive["action"].clone())
        .collect::<Vec<_>>()
        .try_into()
        .expect("parallel reads dispatch exactly two actions");
    let [first_action, second_action] = tool_actions;
    let pending_inspection = inspection_value(&uninterrupted);
    let pending_checkpoint_value = checkpoint_value(&uninterrupted);
    let pending_checkpoint = uninterrupted
        .checkpoint()
        .expect("pending program checkpoint captures");
    let encoded = serde_json::to_string(&pending_checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored =
        HarnessSession::restore(config, decoded, 0).expect("pending program checkpoint restores");
    let restored_pending_inspection = inspection_value(&restored);
    let restored_pending_checkpoint = checkpoint_value(&restored);
    let second_result = programmatic_read_result(&second_action, "second");
    let first_result = programmatic_read_result(&first_action, "first");

    // Do
    let uninterrupted_second = accepted_value(uninterrupted.apply(input(3, second_result.clone())));
    let restored_second = accepted_value(restored.apply(input(1, second_result)));
    let uninterrupted_checkpoint_after_second = checkpoint_value(&uninterrupted);
    let restored_checkpoint_after_second = checkpoint_value(&restored);
    let uninterrupted_completed =
        accepted_value(uninterrupted.apply(input(4, first_result.clone())));
    let restored_completed = accepted_value(restored.apply(input(2, first_result)));

    // Assert
    assert_eq!(
        program_started["transition"]["observations"],
        json!([{
            "type": "tool_execution_started",
            "turn_id": "turn-program",
            "call_id": "program-call",
        }])
    );
    assert_eq!(first_action["type"], "runtime_builtin_tool_call");
    assert_eq!(first_action["call"]["name"], "file_system.read_file");
    assert_eq!(first_action["call"]["arguments"]["path"], "first.txt");
    assert_eq!(second_action["type"], "runtime_builtin_tool_call");
    assert_eq!(second_action["call"]["name"], "file_system.read_file");
    assert_eq!(second_action["call"]["arguments"]["path"], "second.txt");
    assert!(
        !serde_json::to_string(&program_started["transition"]["next"])
            .expect("actions serialize")
            .contains("run_typescript")
    );
    assert_eq!(
        pending_inspection["pending_actions"],
        json!([
            {
                "type": "runtime_builtin_tool_call",
                "action_id": first_action["action_id"],
                "call_id": first_action["call_id"],
                "name": "file_system.read_file",
            },
            {
                "type": "runtime_builtin_tool_call",
                "action_id": second_action["action_id"],
                "call_id": second_action["call_id"],
                "name": "file_system.read_file",
            }
        ])
    );
    assert_eq!(
        restored_pending_inspection["pending_actions"],
        pending_inspection["pending_actions"]
    );
    assert_eq!(restored_pending_checkpoint, pending_checkpoint_value);
    assert_eq!(
        uninterrupted_second["transition"]["observations"],
        restored_second["transition"]["observations"]
    );
    assert_eq!(
        uninterrupted_second["transition"]["observations"][0]["type"],
        "tool_result_committed"
    );
    assert_eq!(
        uninterrupted_second["transition"]["observations"][0]["action_id"],
        second_action["action_id"]
    );
    assert!(
        !serde_json::to_string(&uninterrupted_second["transition"]["observations"])
            .expect("observations serialize")
            .contains("run_typescript")
    );
    assert_eq!(
        uninterrupted_checkpoint_after_second,
        restored_checkpoint_after_second
    );
    assert_eq!(
        uninterrupted_completed["transition"]["observations"],
        restored_completed["transition"]["observations"]
    );
    assert_eq!(
        uninterrupted_completed["transition"]["observations"][0]["type"],
        "tool_result_committed"
    );
    assert_eq!(
        uninterrupted_completed["transition"]["observations"][0]["action_id"],
        first_action["action_id"]
    );
    assert_eq!(
        uninterrupted_completed["transition"]["observations"][1]["type"],
        "tool_execution_finished"
    );
    assert_eq!(
        uninterrupted_completed["transition"]["observations"][1]["call_id"],
        "program-call"
    );
    assert_eq!(
        uninterrupted_completed["transition"]["turn"],
        restored_completed["transition"]["turn"]
    );
    assert_eq!(
        checkpoint_value(&uninterrupted),
        checkpoint_value(&restored)
    );

    let uninterrupted_directive = &uninterrupted_completed["transition"]["next"]["directives"][0];
    let restored_directive = &restored_completed["transition"]["next"]["directives"][0];
    let uninterrupted_action = &uninterrupted_directive["action"];
    let restored_action = &restored_directive["action"];
    assert_eq!(uninterrupted_directive["type"], restored_directive["type"]);
    assert_eq!(
        action_without_model_input(uninterrupted_action),
        action_without_model_input(restored_action)
    );
    assert_eq!(uninterrupted_action["type"], "llm_call");
    assert_eq!(
        uninterrupted_action["model_input"]["messages"]["type"],
        "append"
    );
    assert_eq!(
        restored_action["model_input"]["messages"]["type"],
        "replace"
    );
    assert_eq!(
        uninterrupted_action["model_input"]["tool_catalog"]["type"],
        "keep"
    );
    assert_eq!(
        restored_action["model_input"]["tool_catalog"]["type"],
        "replace"
    );
    assert_eq!(
        uninterrupted_action["model_input"]["tool_catalog"]["revision"],
        restored_action["model_input"]["tool_catalog"]["revision"]
    );
    assert_eq!(
        restored_action["model_input"]["tool_catalog"]["tools"],
        started["transition"]["next"]["directives"][0]["action"]["model_input"]["tool_catalog"]["tools"]
    );
    let uninterrupted_messages = uninterrupted_action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("live completion appends model messages");
    let restored_messages = restored_action["model_input"]["messages"]["messages"]
        .as_array()
        .expect("restored completion replaces model messages");
    assert_eq!(
        &restored_messages[restored_messages.len() - uninterrupted_messages.len()..],
        uninterrupted_messages
    );
    assert!(contains_text(
        uninterrupted_messages,
        r#"{"first":"first","second":"second"}"#
    ));
}

///
/// *Prepare*: A program reads one file, derives the path for a second read, and checkpoints while the first read is pending.
/// *Do*: Restore the checkpoint, return the first result to both sessions, then return the newly dispatched second result.
/// *Assert*: Replay emits the same second action and both sessions finish with identical program output and checkpoints.
///
#[test]
fn sequential_programmatic_rounds_survive_checkpoint_restore() {
    // Prepare
    let config = config();
    let mut live = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(live.apply(input(
        1,
        user_message("turn-sequential-program", "read the derived file", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source = "async function main() { const first = await tools.file_system.read_file({ path: 'first.txt' }); const second = await tools.file_system.read_file({ path: first.content }); return { final: second.content }; }";
    let first_pending = accepted_value(live.apply(input(
        2,
        run_typescript_completion(&completion_action_id, &[("sequential-program", source)]),
    )));
    let first_action = first_pending["transition"]["next"]["directives"][0]["action"].clone();
    let checkpoint = live.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");
    let first_result = programmatic_read_result(&first_action, "second.txt");

    // Do
    let live_second = accepted_value(live.apply(input(3, first_result.clone())));
    let restored_second = accepted_value(restored.apply(input(1, first_result)));
    let live_second_action = live_second["transition"]["next"]["directives"][0]["action"].clone();
    let restored_second_action =
        restored_second["transition"]["next"]["directives"][0]["action"].clone();
    let second_result = programmatic_read_result(&live_second_action, "final contents");
    let live_completed = accepted_value(live.apply(input(4, second_result.clone())));
    let restored_completed = accepted_value(restored.apply(input(2, second_result)));

    // Assert
    assert_eq!(first_action["call"]["arguments"]["path"], "first.txt");
    assert_eq!(live_second_action, restored_second_action);
    assert_eq!(
        live_second_action["call"]["arguments"]["path"],
        "second.txt"
    );
    assert_ne!(live_second_action["action_id"], first_action["action_id"]);
    assert_eq!(checkpoint_value(&live), checkpoint_value(&restored));
    assert_eq!(
        live_completed["transition"]["observations"],
        restored_completed["transition"]["observations"]
    );
    let live_parent_result = parent_tool_message(&live_completed, "sequential-program");
    let restored_parent_result = parent_tool_message(&restored_completed, "sequential-program");
    assert_eq!(live_parent_result, restored_parent_result);
    assert_eq!(live_parent_result["outcome"], "success");
    assert_eq!(
        live_parent_result["content"],
        json!([{"type": "text", "text": "{\"final\":\"final contents\"}"}])
    );
}

///
/// *Prepare*: One assistant completion contains two independent parent `run_typescript` calls, each dispatching one filesystem read.
/// *Do*: Return the second nested result before the first.
/// *Assert*: Execution frames remain separate and final parent tool messages preserve each program's own label and result.
///
#[test]
fn multiple_parent_programs_keep_independent_execution_frames() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-multiple-programs", "run both programs", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let first_source = "async function main() { const result = await tools.file_system.read_file({ path: 'first.txt' }); return { label: 'first', content: result.content }; }";
    let second_source = "async function main() { const result = await tools.file_system.read_file({ path: 'second.txt' }); return { label: 'second', content: result.content }; }";
    let pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(
            &completion_action_id,
            &[
                ("parent-program-first", first_source),
                ("parent-program-second", second_source),
            ],
        ),
    )));
    let actions = pending["transition"]["next"]["directives"]
        .as_array()
        .expect("programs dispatch actions")
        .iter()
        .map(|directive| directive["action"].clone())
        .collect::<Vec<_>>();
    let first_action = actions
        .iter()
        .find(|action| action["call"]["arguments"]["path"] == "first.txt")
        .expect("first program dispatches")
        .clone();
    let second_action = actions
        .iter()
        .find(|action| action["call"]["arguments"]["path"] == "second.txt")
        .expect("second program dispatches")
        .clone();

    // Do
    let second_committed = accepted_value(session.apply(input(
        3,
        programmatic_read_result(&second_action, "second contents"),
    )));
    let completed = accepted_value(session.apply(input(
        4,
        programmatic_read_result(&first_action, "first contents"),
    )));

    // Assert
    assert_ne!(first_action["action_id"], second_action["action_id"]);
    assert_eq!(second_committed["transition"]["next"]["type"], "actions");
    assert_eq!(
        second_committed["transition"]["next"]["directives"][0]["type"],
        "keep"
    );
    let first_action_keep = second_committed["transition"]["next"]["directives"]
        .as_array()
        .expect("next directives are an array")
        .iter()
        .find(|directive| {
            directive["type"] == "keep" && directive["action_id"] == first_action["action_id"]
        })
        .expect("the first nested action remains pending");
    assert_eq!(first_action_keep["action_id"], first_action["action_id"]);
    assert_eq!(
        inspection_value(&session)["pending_actions"][0]["type"],
        "completion"
    );
    let first_result = parent_tool_message(&completed, "parent-program-first");
    let second_result = parent_tool_message(&completed, "parent-program-second");
    assert_eq!(first_result["outcome"], "success");
    assert_eq!(second_result["outcome"], "success");
    assert_eq!(
        first_result["content"],
        json!([{
            "type": "text",
            "text": "{\"label\":\"first\",\"content\":\"first contents\"}"
        }])
    );
    assert_eq!(
        second_result["content"],
        json!([{
            "type": "text",
            "text": "{\"label\":\"second\",\"content\":\"second contents\"}"
        }])
    );
    assert!(
        !serde_json::to_string(&completed["transition"]["observations"])
            .expect("observations serialize")
            .contains("run_typescript")
    );
}

///
/// *Prepare*: Two fresh sessions run TypeScript that either returns locally or throws before requesting any external operation.
/// *Do*: Apply each model completion through the serialized Core interface.
/// *Assert*: Both outcomes remain inside Core, return control to the model, and expose only the private tool result in model context.
///
#[test]
fn local_program_success_and_failure_stay_inside_core() {
    // Prepare
    let cases = [
        (
            "turn-local-success",
            "local-success",
            "async function main() { return { value: 42 }; }",
            "success",
        ),
        (
            "turn-local-failure",
            "local-failure",
            "async function main() { throw new Error('local boom'); }",
            "failure",
        ),
    ];

    for (turn_id, call_id, source, expected_outcome) in cases {
        let mut session = HarnessSession::create(config()).expect("session creates");
        let started = accepted_value(
            session.apply(input(1, user_message(turn_id, "run local code", "queue"))),
        );
        let completion_action_id = dispatched_action_id(&started).to_string();

        // Do
        let result = accepted_value(session.apply(input(
            2,
            run_typescript_completion(&completion_action_id, &[(call_id, source)]),
        )));

        // Assert
        let directives = result["transition"]["next"]["directives"]
            .as_array()
            .expect("local program returns one completion directive");
        assert_eq!(directives[0]["action"]["type"], "llm_call");
        assert!(
            directives
                .iter()
                .all(|directive| directive["action"]["type"] != "runtime_builtin_tool_call")
        );
        let parent_result = parent_tool_message(&result, call_id);
        assert_eq!(parent_result["outcome"], expected_outcome);
        let result_text = parent_result["content"][0]["text"]
            .as_str()
            .expect("parent program result starts with text");
        if expected_outcome == "success" {
            assert_eq!(
                parent_result["content"],
                json!([{
                    "type": "text",
                    "text": "{\"value\":42}"
                }])
            );
        } else {
            assert!(result_text.contains("run_typescript failed"));
            assert!(result_text.contains("local boom"));
        }
        assert_eq!(
            result["transition"]["observations"][0],
            json!({
                "type": "tool_execution_started",
                "turn_id": turn_id,
                "call_id": call_id,
            })
        );
        assert_eq!(
            result["transition"]["observations"][1]["type"],
            "tool_execution_finished"
        );
        assert_eq!(result["transition"]["observations"][1]["call_id"], call_id);
        assert_eq!(
            result["transition"]["observations"][1]["result"]["type"],
            expected_outcome
        );
    }
}

///
/// *Prepare*: A program awaits one filesystem read without catching failures.
/// *Do*: Return a failed Runtime tool result.
/// *Assert*: Core commits the ordinary external result, converts it into a private program failure, and asks the model to continue.
///
#[test]
fn uncaught_programmatic_tool_failure_returns_a_private_program_error() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-program-failure", "read the missing file", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source =
        "async function main() { return tools.file_system.read_file({ path: 'missing.txt' }); }";
    let pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(&completion_action_id, &[("program-failure", source)]),
    )));
    let action = &pending["transition"]["next"]["directives"][0]["action"];

    // Do
    let failed = accepted_value(session.apply(input(
        3,
        json!({
            "type": "tool_failed",
            "action_id": action["action_id"],
            "call_id": action["call_id"],
            "result": {
                "type": "failure",
                "content": [{"type": "text", "text": "missing file diagnostics"}],
                "structured_content": {"path": "missing.txt"},
                "error": {
                    "code": "not_found",
                    "message": "missing.txt was not found",
                    "retryable": false,
                    "details": {"path": "missing.txt"}
                }
            }
        }),
    )));

    // Assert
    assert_eq!(
        failed["transition"]["observations"][0]["type"],
        "tool_result_committed"
    );
    assert_eq!(
        failed["transition"]["observations"][0]["action_id"],
        action["action_id"]
    );
    assert_eq!(
        failed["transition"]["next"]["directives"][0]["action"]["type"],
        "llm_call"
    );
    let parent_result = parent_tool_message(&failed, "program-failure");
    assert_eq!(parent_result["outcome"], "failure");
    let parent_result_text = parent_result["content"][0]["text"]
        .as_str()
        .expect("failed parent program result starts with text");
    assert!(parent_result_text.contains("run_typescript failed"));
    assert!(parent_result_text.contains("missing.txt was not found"));
    assert_eq!(
        failed["transition"]["observations"][1]["type"],
        "tool_execution_finished"
    );
    assert_eq!(
        failed["transition"]["observations"][1]["call_id"],
        "program-failure"
    );
    assert_eq!(
        failed["transition"]["observations"][1]["result"]["type"],
        "failure"
    );
}

///
/// *Prepare*: A checkpointed program logs to stdout and stderr, then receives structured data, visible rich content, user-only content, and client metadata.
/// *Do*: Return the same nested result to the live and restored sessions.
/// *Assert*: Both model projections agree, preserve compact output and log order, retain only visible rich content, and strip client-only data.
///
#[test]
fn program_result_projection_is_safe_and_restore_stable() {
    // Prepare
    let config = config();
    let mut live = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(live.apply(input(
        1,
        user_message("turn-program-projection", "summarize the report", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source = "async function main() { console.log('program stdout'); console.error('program stderr'); const result = await tools.file_system.read_file({ path: 'report.txt' }); return { content: result.content }; }";
    let pending = accepted_value(live.apply(input(
        2,
        run_typescript_completion(&completion_action_id, &[("program-projection", source)]),
    )));
    let action = pending["transition"]["next"]["directives"][0]["action"].clone();
    let checkpoint = live.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");
    let command = json!({
        "type": "tool_succeeded",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": {
            "type": "success",
            "content": [
                {"type": "text", "text": "large intermediary text"},
                {
                    "type": "image",
                    "data": "visible-image",
                    "mimeType": "image/png",
                    "_meta": {"block": "client-visible-only"}
                },
                {
                    "type": "image",
                    "data": "hidden-image",
                    "mimeType": "image/png",
                    "annotations": {"audience": ["user"]},
                    "_meta": {"private": true}
                }
            ],
            "structured_content": {
                "path": "report.txt",
                "content": "summary",
                "file_size_bytes": 7,
                "returned_bytes": 7,
                "offset": 0,
                "lines_read": 1,
                "was_truncated": false
            },
            "_meta": {"request": "client-secret"}
        }
    });

    // Do
    let live_completed = accepted_value(live.apply(input(3, command.clone())));
    let restored_completed = accepted_value(restored.apply(input(1, command)));
    let live_tool_message = parent_tool_message(&live_completed, "program-projection");
    let restored_tool_message = parent_tool_message(&restored_completed, "program-projection");

    // Assert
    assert_eq!(live_tool_message, restored_tool_message);
    assert_eq!(live_tool_message["role"], "tool");
    assert_eq!(live_tool_message["outcome"], "success");
    assert_eq!(
        live_tool_message["content"],
        json!([
            {"type": "text", "text": "{\"content\":\"summary\"}"},
            {"type": "text", "text": "stdout:\nprogram stdout"},
            {"type": "text", "text": "stderr:\nprogram stderr"},
            {
                "type": "text",
                "text": "Additional content from `tools.file_system.read_file` (call 1):"
            },
            {"type": "image", "data": "visible-image", "mimeType": "image/png"}
        ])
    );
    let serialized = serde_json::to_string(live_tool_message).expect("tool message serializes");
    assert!(!serialized.contains("large intermediary text"));
    assert!(!serialized.contains("hidden-image"));
    assert!(!serialized.contains("client-visible-only"));
    assert!(!serialized.contains("client-secret"));
    assert_eq!(checkpoint_value(&live), checkpoint_value(&restored));
}
