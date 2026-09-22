use super::*;

fn config_with_direct_tool_hooks() -> HarnessConfig {
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([
        {
            "id": "pre-tool",
            "point": "pre_tool_call",
            "order": 0,
            "selector": {"type": "always"},
        },
        {
            "id": "post-tool",
            "point": "post_tool_call",
            "order": 1,
            "selector": {"type": "always"},
        },
    ]))
    .expect("tool hook configuration is valid");
    config
}

fn tool_call_completion(action_id: &str, call_id: &str) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id,
        "result": {
            "parts": [{
                "type": "tool_call",
                "id": call_id,
                "name": "read_file",
                "arguments_json": "{\"path\":\"original.txt\"}",
            }],
            "finish_reason": "tool_call",
            "usage": null,
        },
    })
}

fn tool_succeeded(action_id: &str, call_id: &str, text: &str) -> Value {
    json!({
        "type": "tool_succeeded",
        "action_id": action_id,
        "call_id": call_id,
        "result": {
            "type": "success",
            "content": [{"type": "text", "text": text}],
        },
    })
}

fn hook_failed(action_id: &str, code: &str, message: &str) -> Value {
    json!({
        "type": "hook_failed",
        "action_id": action_id,
        "error": {
            "code": code,
            "message": message,
            "retryable": false,
            "details": null,
        },
    })
}

fn pending_direct_pre_hook(
    config: HarnessConfig,
    turn_id: &str,
    call_id: &str,
) -> (HarnessSession, String) {
    let mut session = HarnessSession::create(config).expect("session creates");
    let started =
        accepted_value(session.apply(input(1, user_message(turn_id, "read the file", "queue"))));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let pending = accepted_value(session.apply(input(
        2,
        tool_call_completion(&completion_action_id, call_id),
    )));
    (session, dispatched_action_id(&pending).to_string())
}

///
/// *Prepare*: Provided tool groups use a reserved namespace, a built-in direct name, or the same direct name twice.
/// *Do*: Create a Harness session from each invalid configuration.
/// *Assert*: Session creation rejects each collision with the existing exact error.
///
#[test]
fn provided_tool_name_collisions_reject_session_creation() {
    // Prepare
    let tool = |name: &str| {
        json!({
            "name": name,
            "description": "Provided tool.",
            "input_schema": {"type": "object"},
            "exposure": "direct",
        })
    };
    let mut reserved_group = config();
    reserved_group.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "file_system",
        "tools": [],
    }]))
    .expect("reserved group remains structurally valid");
    let mut reserved_direct_name = config();
    reserved_direct_name.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "client",
        "tools": [tool("bash")],
    }]))
    .expect("reserved direct tool remains structurally valid");
    let mut duplicate_direct_name = config();
    duplicate_direct_name.capabilities.tool_groups = serde_json::from_value(json!([
        {"name": "first", "tools": [tool("choose")]},
        {"name": "second", "tools": [tool("choose")]},
    ]))
    .expect("duplicate direct tools remain structurally valid");
    let cases = [
        (
            reserved_group,
            "tool group name \"file_system\" is reserved",
        ),
        (
            reserved_direct_name,
            "direct tool name \"bash\" conflicts with a built-in tool",
        ),
        (
            duplicate_direct_name,
            "ambiguous duplicate direct tool name \"choose\"",
        ),
    ];

    // Do
    let errors = cases.map(|(config, expected)| {
        let error = HarnessSession::create(config)
            .err()
            .expect("invalid provided tools reject session creation");
        (error.to_string(), expected)
    });

    // Assert
    for (actual, expected) in errors {
        assert_eq!(actual, expected);
    }
}

///
/// *Prepare*: A direct filesystem call is pending with a private path argument.
/// *Do*: Inspect the session through the public Core interface.
/// *Assert*: Inspection identifies the pending Runtime tool but exposes neither its arguments nor the private path.
///
#[test]
fn tool_inspection_identifies_the_route_without_exposing_arguments() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-inspection", "read the private file", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    accepted_value(session.apply(input(
        2,
        json!({
            "type": "completion_succeeded",
            "action_id": completion_action_id,
            "result": {
                "parts": [{
                    "type": "tool_call",
                    "id": "call-private-file",
                    "name": "read_file",
                    "arguments_json": "{\"path\":\"secret.txt\"}",
                }],
                "finish_reason": "tool_call",
                "usage": null,
            },
        }),
    )));

    // Do
    let inspection = inspection_value(&session);
    let serialized = serde_json::to_string(&inspection).expect("inspection serializes");

    // Assert
    assert_eq!(
        inspection["pending_actions"][0]["type"],
        "runtime_builtin_tool_call"
    );
    assert_eq!(
        inspection["pending_actions"][0]["name"],
        "file_system.read_file"
    );
    assert!(!serialized.contains("secret.txt"));
    assert!(!serialized.contains("arguments"));
}

///
/// *Prepare*: A direct filesystem call is configured with pre-tool and post-tool hooks.
/// *Do*: Rewrite its arguments, return a raw result, then complete the post-tool hook with a transformed result.
/// *Assert*: Only the transformed result is committed and included in the next serialized model input.
///
#[test]
fn direct_tool_hooks_commit_only_the_transformed_result() {
    // Prepare
    let mut session =
        HarnessSession::create(config_with_direct_tool_hooks()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-hooks", "read the file", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let pre_hook_pending = accepted_value(session.apply(input(
        2,
        tool_call_completion(&completion_action_id, "call-hooks"),
    )));
    let pre_hook_action = &pre_hook_pending["transition"]["next"]["directives"][0]["action"];
    let pre_hook_action_id = pre_hook_action["action_id"]
        .as_str()
        .expect("pre-tool hook action has an ID")
        .to_string();

    // Do
    let tool_pending = accepted_value(session.apply(input(
        3,
        json!({
            "type": "hook_completed",
            "action_id": pre_hook_action_id,
            "result": {
                "hook": "pre_tool_call",
                "output": {
                    "type": "continue",
                    "effective_arguments": {"path": "effective.txt"},
                },
            },
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
    let post_hook_pending = accepted_value(session.apply(input(
        4,
        tool_succeeded(&tool_action_id, &call_id, "raw private result"),
    )));
    let post_hook_action = &post_hook_pending["transition"]["next"]["directives"][0]["action"];
    let post_hook_action_id = post_hook_action["action_id"]
        .as_str()
        .expect("post-tool hook action has an ID")
        .to_string();
    let transformed = accepted_value(session.apply(input(
        5,
        json!({
            "type": "hook_completed",
            "action_id": post_hook_action_id,
            "result": {
                "hook": "post_tool_call",
                "output": {
                    "tool_result": {
                        "type": "success",
                        "content": [{"type": "text", "text": "transformed public result"}],
                    },
                },
            },
        }),
    )));

    // Assert
    assert_eq!(pre_hook_action["type"], "hook_call");
    assert_eq!(pre_hook_action["hook"], "pre_tool_call");
    assert_eq!(tool_action["type"], "runtime_builtin_tool_call");
    assert_eq!(tool_action["call"]["arguments"]["path"], "effective.txt");
    assert_eq!(post_hook_action["type"], "hook_call");
    assert_eq!(post_hook_action["hook"], "post_tool_call");
    assert_eq!(post_hook_pending["transition"]["observations"], json!([]));
    let committed = &transformed["transition"]["observations"][0];
    assert_eq!(committed["type"], "tool_result_committed");
    assert_eq!(
        committed["result"]["content"][0]["text"],
        "transformed public result"
    );
    let serialized_observations = serde_json::to_string(&transformed["transition"]["observations"])
        .expect("observations serialize");
    let next_model_input =
        &transformed["transition"]["next"]["directives"][0]["action"]["model_input"];
    let serialized_model_input =
        serde_json::to_string(next_model_input).expect("model input serializes");
    assert!(serialized_observations.contains("transformed public result"));
    assert!(!serialized_observations.contains("raw private result"));
    assert!(serialized_model_input.contains("transformed public result"));
    assert!(!serialized_model_input.contains("raw private result"));
}

///
/// *Prepare*: A direct filesystem call is waiting on a pre-tool hook.
/// *Do*: Continue the hook with arguments that violate the original Core-owned schema.
/// *Assert*: The serialized command is rejected with the existing exact error and the pending hook remains unchanged.
///
#[test]
fn invalid_pre_tool_hook_arguments_preserve_the_pending_call() {
    // Prepare
    let (mut session, hook_action_id) = pending_direct_pre_hook(
        config_with_direct_tool_hooks(),
        "turn-invalid-hook-arguments",
        "call-invalid-hook-arguments",
    );
    let before = inspection_value(&session);

    // Do
    let rejected = serde_json::to_value(session.apply(input(
        3,
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
    let after = inspection_value(&session);

    // Assert
    assert_eq!(
        rejected,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "invalid_command",
                "error": {
                    "code": "invalid_command",
                    "message": "pre-tool hook arguments do not satisfy the original tool schema: [] is not of type \"object\"",
                    "retryable": false,
                    "details": null,
                },
            },
        })
    );
    assert_eq!(after, before);
}

///
/// *Prepare*: A provided direct tool with a Core-tracked input schema is waiting on a pre-tool hook.
/// *Do*: Continue the hook with arguments that violate that configured schema.
/// *Assert*: The provided-tool contract is resolved from the session catalog and rejected without advancing the call.
///
#[test]
fn invalid_provided_tool_hook_arguments_use_the_resolved_catalog() {
    // Prepare
    let mut config = config_with_direct_tool_hooks();
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
            "exposure": "direct",
        }],
    }]))
    .expect("provided tool configuration is valid");
    let mut session = HarnessSession::create(config).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-invalid-provided-hook", "find the event", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let hook_pending = accepted_value(session.apply(input(
        2,
        json!({
            "type": "completion_succeeded",
            "action_id": completion_action_id,
            "result": {
                "parts": [{
                    "type": "tool_call",
                    "id": "call-invalid-provided-hook",
                    "name": "find_event",
                    "arguments_json": "{\"query\":\"planning\"}",
                }],
                "finish_reason": "tool_call",
                "usage": null,
            },
        }),
    )));
    let hook_action_id = dispatched_action_id(&hook_pending).to_string();
    let before = inspection_value(&session);

    // Do
    let rejected = serde_json::to_value(session.apply(input(
        3,
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
    assert_eq!(inspection_value(&session), before);
}

///
/// *Prepare*: A direct filesystem call is pending at the Runtime boundary.
/// *Do*: Report successful structured output that violates the Core-owned output schema.
/// *Assert*: Core normalizes it to the existing exact recoverable failure before committing it.
///
#[test]
fn invalid_runtime_builtin_output_preserves_the_exact_failure() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-invalid-output", "read the file", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let tool_pending = accepted_value(session.apply(input(
        2,
        tool_call_completion(&completion_action_id, "call-invalid-output"),
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

    // Do
    let normalized = accepted_value(session.apply(input(
        3,
        json!({
            "type": "tool_succeeded",
            "action_id": tool_action_id,
            "call_id": call_id,
            "result": {
                "type": "success",
                "content": [],
                "structured_content": [],
            },
        }),
    )));

    // Assert
    let committed = &normalized["transition"]["observations"][0];
    assert_eq!(committed["type"], "tool_result_committed");
    assert_eq!(committed["result"]["type"], "failure");
    assert_eq!(
        committed["result"]["error"],
        json!({
            "code": "runtime_builtin_output_schema_mismatch",
            "message": "Runtime built-in file_system.read_file returned structured content that does not satisfy its Core-owned output schema: [] is not of type \"object\"",
            "retryable": false,
            "details": null,
        })
    );
    assert_eq!(
        normalized["transition"]["next"]["directives"][0]["action"]["type"],
        "llm_call"
    );
}

///
/// *Prepare*: A direct filesystem action is pending for an active turn.
/// *Do*: Interrupt the turn, then submit the pending action's later tool result.
/// *Assert*: The interruption reports action_abandoned and the stale result is rejected without a pending correlation.
///
#[test]
fn interruption_abandons_a_pending_direct_tool_and_rejects_its_stale_result() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-interrupt-tool", "read the file", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let tool_pending = accepted_value(session.apply(input(
        2,
        tool_call_completion(&completion_action_id, "call-interrupt"),
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

    // Do
    let interrupted = accepted_value(session.apply(input(
        3,
        json!({
            "type": "interrupt",
            "expected_turn_id": "turn-interrupt-tool",
            "reason": "stop now",
        }),
    )));
    let stale_result = serde_json::to_value(session.apply(input(
        4,
        tool_succeeded(&tool_action_id, &call_id, "late result"),
    )))
    .expect("rejection serializes");

    // Assert
    assert_eq!(interrupted["transition"]["turn"]["status"], "interrupted");
    assert_eq!(
        abandoned_actions(&interrupted),
        vec![(tool_action_id.as_str(), "interrupt")]
    );
    assert_eq!(
        stale_result,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "invalid_correlation",
                "received_action_id": tool_action_id,
                "pending_action_ids": [],
            },
        })
    );
}

///
/// *Prepare*: A restored direct filesystem call is waiting on its pre-tool hook.
/// *Do*: Skip the call with a policy reason.
/// *Assert*: The Core commits a skipped failure result, dispatches the next model call, and never emits a Runtime tool action.
///
#[test]
fn restored_direct_pre_tool_skip_commits_failure_without_runtime_execution() {
    // Prepare
    let config = config_with_direct_tool_hooks();
    let (session, hook_action_id) =
        pending_direct_pre_hook(config.clone(), "turn-direct-skip", "call-direct-skip");
    let checkpoint = session
        .checkpoint()
        .expect("pending pre-tool hook checkpoints");
    let mut restored = HarnessSession::restore(config, checkpoint, 0).expect("checkpoint restores");

    // Do
    let skipped = accepted_value(restored.apply(input(
        1,
        json!({
            "type": "hook_completed",
            "action_id": hook_action_id,
            "result": {
                "hook": "pre_tool_call",
                "output": {
                    "type": "skip",
                    "reason": [{"type": "text", "text": "file access denied"}],
                },
            },
        }),
    )));

    // Assert
    let observations = skipped["transition"]["observations"]
        .as_array()
        .expect("observations are an array");
    assert_eq!(observations.len(), 2);
    assert_eq!(observations[0]["type"], "tool_result_committed");
    assert_eq!(observations[0]["call_id"], "call-direct-skip");
    assert_eq!(observations[0]["result"]["type"], "failure");
    assert_eq!(observations[0]["result"]["error"]["code"], "tool_skipped");
    assert_eq!(
        observations[0]["result"]["content"][0]["text"],
        "file access denied"
    );
    assert_eq!(observations[1]["type"], "tool_execution_finished");
    assert_eq!(observations[1]["call_id"], "call-direct-skip");
    assert_eq!(observations[1]["result"], observations[0]["result"]);
    let next_action = &skipped["transition"]["next"]["directives"][0]["action"];
    assert_eq!(next_action["type"], "llm_call");
    let model_input =
        serde_json::to_string(&next_action["model_input"]).expect("model input serializes");
    assert!(model_input.contains("file access denied"));
    assert_eq!(skipped["transition"]["turn"]["status"], "running");
}

///
/// *Prepare*: One direct call is waiting on a pre-tool hook and another has returned a raw result to a post-tool hook.
/// *Do*: Fail each hook through serialized HarnessSession inputs.
/// *Assert*: Both paths commit only the hook failure, pre-hook failure emits no Runtime call, and post-hook failure hides the raw result.
///
#[test]
fn direct_hook_failures_commit_only_the_hook_error() {
    // Prepare
    let config = config_with_direct_tool_hooks();
    let (mut pre_session, pre_hook_action_id) = pending_direct_pre_hook(
        config.clone(),
        "turn-direct-pre-failure",
        "call-direct-pre-failure",
    );

    let (mut post_session, post_pre_hook_action_id) = pending_direct_pre_hook(
        config,
        "turn-direct-post-failure",
        "call-direct-post-failure",
    );
    let tool_pending = accepted_value(post_session.apply(input(
        3,
        json!({
            "type": "hook_completed",
            "action_id": post_pre_hook_action_id,
            "result": {
                "hook": "pre_tool_call",
                "output": {
                    "type": "continue",
                    "effective_arguments": {"path": "effective.txt"},
                },
            },
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
    let post_hook_pending = accepted_value(post_session.apply(input(
        4,
        tool_succeeded(&tool_action_id, &call_id, "raw result must stay private"),
    )));
    let post_hook_action_id = dispatched_action_id(&post_hook_pending).to_string();

    // Do
    let pre_failed = accepted_value(pre_session.apply(input(
        3,
        hook_failed(
            &pre_hook_action_id,
            "pre_hook_failed",
            "pre hook denied the call",
        ),
    )));
    let post_failed = accepted_value(post_session.apply(input(
        5,
        hook_failed(
            &post_hook_action_id,
            "post_hook_failed",
            "post hook rejected the result",
        ),
    )));

    // Assert
    for (result, call_id, code, message) in [
        (
            &pre_failed,
            "call-direct-pre-failure",
            "pre_hook_failed",
            "pre hook denied the call",
        ),
        (
            &post_failed,
            "call-direct-post-failure",
            "post_hook_failed",
            "post hook rejected the result",
        ),
    ] {
        let observations = result["transition"]["observations"]
            .as_array()
            .expect("observations are an array");
        assert_eq!(observations.len(), 2);
        assert_eq!(observations[0]["type"], "tool_result_committed");
        assert_eq!(observations[0]["call_id"], call_id);
        assert_eq!(observations[0]["result"]["type"], "failure");
        assert_eq!(observations[0]["result"]["error"]["code"], code);
        assert_eq!(observations[0]["result"]["content"][0]["text"], message);
        assert_eq!(observations[1]["type"], "tool_execution_finished");
        assert_eq!(observations[1]["call_id"], call_id);
        assert_eq!(observations[1]["result"], observations[0]["result"]);
        let action = &result["transition"]["next"]["directives"][0]["action"];
        assert_eq!(action["type"], "llm_call");
        let model_input =
            serde_json::to_string(&action["model_input"]).expect("model input serializes");
        assert!(model_input.contains(message));
    }
    let pre_serialized = serde_json::to_string(&pre_failed).expect("transition serializes");
    assert!(!pre_serialized.contains("runtime_builtin_tool_call"));
    let post_serialized = serde_json::to_string(&post_failed).expect("transition serializes");
    assert!(!post_serialized.contains("raw result must stay private"));
}
