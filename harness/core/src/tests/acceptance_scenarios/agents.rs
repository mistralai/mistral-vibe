use super::*;

fn attributed_agent_content(kind: &str, sender: &str, text: &str) -> Value {
    json!([
        {"type":"text", "text":format!("Agent {kind} from {sender}:\n")},
        {"type":"text", "text":text},
    ])
}

/// *Prepare*: An attributed task starts a Turn, then another agent steers its pending completion.
/// *Do*: Restore before the first completion finishes and let ordinary steering reach the next request.
/// *Assert*: Core keeps one Turn and renders both attributed inputs once without internal identities.
#[test]
fn agent_message_start_and_steering_share_turn_semantics_through_restore() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let task = attributed_agent_content("task", "/root", "investigate");
    let first = runtime.apply(json!({
        "type":"agent_message", "turn_id":"child-turn", "mode":"queue",
        "message":{"sender_agent_path":"/root", "kind":"task", "content":[{"type":"text", "text":"investigate"}]},
    }), running("child-turn").dispatch(llm_call(0)).observe(json!({
        "type":"turn_started", "turn_id":"child-turn", "content":task,
    }))).only_action();
    let message = attributed_agent_content("message", "/root/sibling", "new evidence");

    // Do
    runtime.apply(json!({
        "type":"agent_message", "turn_id":"child-turn", "mode":"steer",
        "message":{"sender_agent_path":"/root/sibling", "kind":"message", "content":[{"type":"text", "text":"new evidence"}]},
    }), running("child-turn").keep(&first).observe(json!({
        "type":"turn_steering_received", "turn_id":"child-turn", "content":message,
    })));
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], true);
    runtime.restart_from_checkpoint();
    let next = runtime
        .apply(
            text_completion(&first, "initial finding"),
            running("child-turn")
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(
                    "child-turn",
                    &first,
                    "initial finding",
                ))
                .observe(json!({"type":"turn_steered", "turn_id":"child-turn", "content":message})),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &next,
        "replace",
        &[
            model_system(),
            json!({"role":"user", "content":task}),
            model_assistant_text("initial finding"),
            json!({"role":"user", "content":message}),
        ],
    );
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], false);
    runtime.finish_turn_with_text("child-turn", &next, "incorporated");
}

/// *Prepare*: An idle Session retains attributed input in its bounded notification state.
/// *Do*: Restore, retry the same delivery, then start a Turn and retry after model-context commit.
/// *Assert*: The input precedes new work once, and retries do not make model input unseen again.
#[test]
fn pending_agent_message_survives_restore_and_is_seen_once_before_new_input() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let notification = json!({
        "id":"delivery", "source":{"type":"agent_message", "sender_agent_path":"/root/child", "kind":"message"},
        "level":"info", "message":"Agent input", "content":[{"type":"text", "text":"retained advice"}],
    });
    let command = json!({"type":"notification", "notification":notification});
    runtime.apply(
        command.clone(),
        idle().observe(json!({"type":"notification_received", "notification":notification})),
    );
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], true);

    assert_eq!(
        runtime.checkpoint_value()["notifications"][0]["notification"]["source"],
        json!({
            "type":"agent_message", "sender_agent_path":"/root/child", "kind":"message",
        }),
    );

    // Do
    runtime.restart_from_checkpoint();
    runtime.apply(command.clone(), idle());
    let first = runtime
        .apply(
            user_message("parent-turn", "new work", "queue"),
            running("parent-turn")
                .dispatch(llm_call(0))
                .observe(turn_started("parent-turn", "new work"))
                .observe(notification_delivered("parent-turn", notification)),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(&first, "replace", &[
        model_system(),
        json!({"role":"user", "content":attributed_agent_content("message", "/root/child", "retained advice")}),
        model_user_text("new work"),
    ]);
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], false);
    runtime.restart_from_checkpoint();
    runtime.apply(command, running("parent-turn").keep(&first));
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], false);
    runtime.finish_turn_with_text("parent-turn", &first, "done");
}

/// *Prepare*: An idle Session receives a typed result without starting a Turn.
/// *Do*: Restore, retry delivery, and start ordinary user work.
/// *Assert*: Core retains the exact result and renders it once without delivery identities.
#[test]
fn agent_results_survive_restore_and_are_seen_once_before_new_input() {
    for (termination, rendered) in [
        (
            json!({"outcome":"completed", "output":{"answer":42}}),
            "Status: completed\nOutput: {\"answer\":42}",
        ),
        (
            json!({"outcome":"completed", "output":null}),
            "Status: completed\nOutput: null",
        ),
        (
            json!({"outcome":"failed", "error":{"message":"Provider failed", "code":"unavailable", "details":{"attempt":2}}}),
            "Status: failed\nError: {\"message\":\"Provider failed\",\"code\":\"unavailable\",\"details\":{\"attempt\":2}}",
        ),
        (
            json!({"outcome":"failed", "error":{"message":"Unknown failure", "details":null}}),
            "Status: failed\nError: {\"message\":\"Unknown failure\",\"details\":null}",
        ),
        (
            json!({"outcome":"cancelled", "reason":"User request"}),
            "Status: cancelled\nReason: User request",
        ),
        (json!({"outcome":"cancelled"}), "Status: cancelled"),
    ] {
        // Prepare
        let mut runtime = SynchronousRuntime::new(config());
        let source = json!({
            "type":"agent_result", "sender_agent_path":"/root/child",
            "termination":termination,
        });
        let notification = json!({
            "id":"private-delivery-id", "source":source,
            "level":"info", "message":"Private delivery summary",
        });
        let command = json!({"type":"notification", "notification":notification});
        runtime.apply(
            command.clone(),
            idle().observe(json!({"type":"notification_received", "notification":notification})),
        );
        assert_eq!(runtime.inspection_value()["has_pending_model_input"], true);
        assert_eq!(
            runtime.checkpoint_value()["notifications"][0]["notification"]["source"],
            source
        );

        // Do
        runtime.restart_from_checkpoint();
        runtime.apply(command.clone(), idle());
        let first = runtime
            .apply(
                user_message("parent-turn", "new work", "queue"),
                running("parent-turn")
                    .dispatch(llm_call(0))
                    .observe(turn_started("parent-turn", "new work"))
                    .observe(notification_delivered("parent-turn", notification)),
            )
            .only_action();

        // Assert
        runtime.assert_last_model_message_update(&first, "replace", &[
        model_system(),
        json!({"role":"user", "content":attributed_agent_content("result", "/root/child", rendered)}),
        model_user_text("new work"),
    ]);
        assert_eq!(runtime.inspection_value()["has_pending_model_input"], false);
        runtime.restart_from_checkpoint();
        runtime.apply(command, running("parent-turn").keep(&first));
        assert_eq!(runtime.inspection_value()["has_pending_model_input"], false);
        runtime.finish_turn_with_text("parent-turn", &first, "done");
    }
}

/// *Prepare*: A result starts a Turn and another result steers its pending completion.
/// *Do*: Restore before accepting the completion and continue the existing Turn.
/// *Assert*: Typed results use ordinary Turn observations and appear once in model context.
#[test]
fn agent_result_start_and_steering_share_turn_semantics_through_restore() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let cancelled = attributed_agent_content(
        "result",
        "/root/first",
        "Status: cancelled\nReason: Superseded",
    );
    let first = runtime.apply(json!({
        "type":"agent_message", "turn_id":"parent-turn", "mode":"queue",
        "message":{"sender_agent_path":"/root/first", "kind":"result", "termination":{"outcome":"cancelled", "reason":"Superseded"}},
    }), running("parent-turn").dispatch(llm_call(0)).observe(json!({
        "type":"turn_started", "turn_id":"parent-turn", "content":cancelled,
    }))).only_action();
    let failed = attributed_agent_content(
        "result",
        "/root/second",
        "Status: failed\nError: {\"message\":\"No access\",\"details\":null}",
    );

    // Do
    runtime.apply(json!({
        "type":"agent_message", "turn_id":"parent-turn", "mode":"steer",
        "message":{"sender_agent_path":"/root/second", "kind":"result", "termination":{"outcome":"failed", "error":{"message":"No access"}}},
    }), running("parent-turn").keep(&first).observe(json!({
        "type":"turn_steering_received", "turn_id":"parent-turn", "content":failed,
    })));
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], true);
    runtime.restart_from_checkpoint();
    let next = runtime
        .apply(
            text_completion(&first, "initial response"),
            running("parent-turn")
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(
                    "parent-turn",
                    &first,
                    "initial response",
                ))
                .observe(json!({"type":"turn_steered", "turn_id":"parent-turn", "content":failed})),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &next,
        "replace",
        &[
            model_system(),
            json!({"role":"user", "content":cancelled}),
            model_assistant_text("initial response"),
            json!({"role":"user", "content":failed}),
        ],
    );
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], false);
    runtime.finish_turn_with_text("parent-turn", &next, "done");
}

/// *Prepare*: The parent model is waiting when a child result is admitted.
/// *Do*: Restore and return the Runtime's ordinary input-available wait result.
/// *Assert*: The wait Action is preserved and the result reaches the same Turn exactly once.
#[test]
fn agent_result_notification_preserves_wait_and_reaches_its_next_model_request() {
    // Prepare
    let mut configuration = config();
    configuration.settings.tools.model_wait =
        serde_json::from_value(json!({"mode":"enabled"})).unwrap();
    let mut runtime = SynchronousRuntime::new(configuration);
    let first = runtime.start_turn("parent-turn", "wait for child");
    let calls = vec![tool_call("wait-1", "wait", json!({"timeout_ms":30000}))];
    let wait = runtime
        .apply_at(
            tool_call_completion(action_id(&first), calls.clone()),
            1_700_000_000_000,
            running("parent-turn")
                .dispatch(runtime_tool(
                    "self.wait",
                    json!({"timeout_ms":30000, "deadline_unix_ms":1_700_000_030_000_u64}),
                ))
                .observe(assistant_tool_calls_committed(
                    "parent-turn",
                    &first,
                    &calls,
                ))
                .observe(tool_execution_started("parent-turn", "wait-1")),
        )
        .only_action();
    let notification = json!({
        "id":"private-delivery-id", "level":"info", "message":"Private summary",
        "source":{"type":"agent_result", "sender_agent_path":"/root/child", "termination":{"outcome":"completed", "output":"found it"}},
    });

    // Do
    runtime.apply(
        json!({"type":"notification", "notification":notification}),
        running("parent-turn").keep(&wait).observe(json!({
            "type":"notification_received", "turn_id":"parent-turn", "notification":notification,
        })),
    );
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], true);
    runtime.restart_from_checkpoint();
    let response = structured_tool_success(&wait, json!({"reason":"input_available"}));
    let next = runtime.apply(response.clone(),
        running("parent-turn").dispatch(llm_call(1))
            .observe_tool_result(&wait, &response)
            .observe_tool_execution_finished("wait-1", json!({"type":"success", "content":[], "structured_content":{"reason":"input_available"}}))
            .observe(notification_delivered("parent-turn", notification.clone())),
    ).only_action();

    // Assert
    runtime.assert_last_model_message_update(&next, "replace", &[
        model_system(), model_user_text("wait for child"), model_assistant_tool_calls(&calls),
        model_tool_text("wait-1", "wait", "{\"reason\":\"input_available\"}"),
        json!({"role":"user", "content":attributed_agent_content("result", "/root/child", "Status: completed\nOutput: \"found it\"")}),
    ]);
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], false);
    runtime.apply(
        json!({"type":"notification", "notification":notification}),
        running("parent-turn").keep(&next),
    );
    assert_eq!(runtime.inspection_value()["has_pending_model_input"], false);
    runtime.finish_turn_with_text("parent-turn", &next, "done");
}

fn frozen_agent_action(key: &str) -> Value {
    let actions: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/checkpoints/retired-v1-agents/deprecated-agent-actions.json"
    )))
    .unwrap();
    actions[key].clone()
}

/// *Prepare*: An earlier V1 program has received an image-bearing spawn result and is awaiting its post-hook.
/// *Do*: Restore the Hook Action, preserve its result, and resume the program.
/// *Assert*: The old Action/result survive unchanged and the image reaches the model without new effects.
#[test]
fn frozen_deprecated_agent_post_hook_retains_rich_result() {
    let mut settings = config();
    settings.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id":"saved-post", "point":"post_tool_call", "order":0, "selector":{"type":"always"}
    }]))
    .unwrap();
    let hook: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/checkpoints/retired-v1-agents/deprecated-agent-post-hook-action.json"
    )))
    .unwrap();
    let fixture = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/checkpoints/retired-v1-agents/active-deprecated-agent-post-hook.json"
    ));
    let mut runtime = SynchronousRuntime::from_checkpoint(settings, fixture, vec![hook.clone()]);
    assert_eq!(
        runtime.checkpoint_value(),
        serde_json::from_str::<Value>(fixture).unwrap()
    );
    runtime.restart_from_checkpoint();
    let result = hook["input"]["tool_result"].clone();
    let expected = json!({
        "type":"success", "structured_content":{"type":"success"},
        "content":[
            {"type":"text", "text":"{\"type\":\"success\"}"},
            {"type":"text", "text":"Additional content from `tools.agent.spawn` (call 1):"},
            {"type":"image", "data":"aW1hZ2U=", "mimeType":"image/png"},
        ],
    });
    let completion = runtime
        .apply(
            hook_completed(&hook, "post_tool_call", json!({"tool_result":result})),
            running("old-agent")
                .dispatch(llm_call(1))
                .observe(tool_result_committed(
                    "old-agent",
                    &frozen_agent_action("pending_action"),
                    result,
                ))
                .observe_tool_execution_finished("program", expected),
        )
        .only_action();
    runtime.finish_turn_with_text("old-agent", &completion, "done");
}

/// *Prepare*: A frozen V1 program has an issued spawn using the deprecated spelling.
/// *Do*: Restore with current configuration and deliver its previously completed result.
/// *Assert*: The already-issued Action keeps its identity; its result advances to the timer once.
#[test]
fn frozen_deprecated_agent_spawn_preserves_pending_identity() {
    let spawn = frozen_agent_action("pending_action");
    let fixture = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/checkpoints/retired-v1-agents/active-deprecated-agent-spawn.json"
    ));
    let mut runtime = SynchronousRuntime::from_checkpoint(config(), fixture, vec![spawn.clone()]);
    assert_eq!(
        runtime.checkpoint_value(),
        serde_json::from_str::<Value>(fixture).unwrap()
    );
    runtime.restart_from_checkpoint();
    let response = structured_tool_success(&spawn, json!({"type":"success"}));
    let timer = runtime
        .apply(
            response.clone(),
            running("old-agent")
                .dispatch(runtime_tool("self.sleep", json!({"seconds":1})))
                .observe_tool_result(&spawn, &response),
        )
        .only_action();
    assert_eq!(
        timer["call"],
        frozen_agent_action("completed_action")["call"]
    );
    runtime.restart_from_checkpoint();
    let response = structured_tool_success(&timer, json!({"seconds":1}));
    let completion = runtime
        .apply(
            response.clone(),
            running("old-agent")
                .dispatch(llm_call(1))
                .observe_tool_result(&timer, &response)
                .observe_tool_execution_finished(
                    "program",
                    run_typescript_success(json!({
                        "first":{"type":"success"},
                        "error":"tools.agent.spawn is deprecated; use tools.agents.spawn.",
                    })),
                ),
        )
        .only_action();
    runtime.finish_turn_with_text("old-agent", &completion, "done");
}

/// *Prepare*: A frozen V1 program already spawned a child and is awaiting a timer.
/// *Do*: Complete the timer, replay the saved spawn result, then attempt another deprecated call.
/// *Assert*: The saved result remains available and the new deprecated call does not dispatch.
#[test]
fn frozen_completed_deprecated_agent_spawn_replays_without_new_effects() {
    let timer = frozen_agent_action("completed_action");
    let mut runtime = SynchronousRuntime::from_checkpoint(
        config(),
        include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/checkpoints/retired-v1-agents/active-completed-deprecated-agent-spawn.json"
        )),
        vec![timer.clone()],
    );
    let response = structured_tool_success(&timer, json!({"seconds":1}));
    runtime.apply(
        response.clone(),
        running("old-agent")
            .dispatch(llm_call(1))
            .observe_tool_result(&timer, &response)
            .observe_tool_execution_finished(
                "program",
                run_typescript_success(json!({
                    "first":{"type":"success"},
                    "error":"tools.agent.spawn is deprecated; use tools.agents.spawn.",
                })),
            ),
    );
}

/// *Prepare*: A frozen V1 program used the retired `tools.subagent` spelling, already spawned a child, and is awaiting a timer.
/// *Do*: Complete the timer, replay the saved spawn result, then attempt another call through that spelling.
/// *Assert*: The saved result replays under its recorded runtime name and the new call does not dispatch.
#[test]
fn frozen_completed_retired_subagent_spawn_replays_without_new_effects() {
    let timer = frozen_agent_action("completed_action");
    let fixture = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/checkpoints/retired-v1-agents/active-completed-deprecated-agent-spawn.json"
    ))
    .replace("tools.agent.spawn", "tools.subagent.spawn");
    let mut runtime = SynchronousRuntime::from_checkpoint(config(), &fixture, vec![timer.clone()]);
    let response = structured_tool_success(&timer, json!({"seconds":1}));
    runtime.apply(
        response.clone(),
        running("old-agent")
            .dispatch(llm_call(1))
            .observe_tool_result(&timer, &response)
            .observe_tool_execution_finished(
                "program",
                run_typescript_success(json!({
                    "first":{"type":"success"},
                    "error":"tools.subagent.spawn is deprecated; tools.agents.spawn.",
                })),
            ),
    );
}

/// *Prepare*: Each canonical agent operation is selected by a model program.
/// *Do*: Restore its pending Action and deliver one correlated Runtime result.
/// *Assert*: Core preserves the exact route, arguments, observations, and returned value.
#[test]
fn canonical_agent_operations_survive_restore() {
    let cases = [
        (
            "list_configs",
            json!({}),
            json!({"configs": [{"name":"researcher", "description":"Find evidence"}]}),
        ),
        ("list", json!({}), json!({"agents": []})),
        (
            "spawn",
            json!({"name": "child", "message": "work", "config_name": "researcher"}),
            json!({"type": "success"}),
        ),
        (
            "send_message",
            json!({"agent_path": "/root/child", "message": "more"}),
            json!({"type": "success"}),
        ),
        (
            "interrupt",
            json!({"name": "child"}),
            json!({"type": "success"}),
        ),
    ];
    for (operation, arguments, result) in cases {
        // Prepare
        let turn_id = "agent-operation";
        let mut runtime = SynchronousRuntime::new(config());
        let first = runtime.start_turn(turn_id, "coordinate");
        let source =
            format!("async function main() {{ return tools.agents.{operation}({arguments}); }}");
        let pending = runtime
            .complete_with_typescript_program(
                turn_id,
                &first,
                "program",
                &source,
                [runtime_tool(
                    &format!("agents.{operation}"),
                    arguments.clone(),
                )],
            )
            .only_action();

        // Do
        runtime.restart_from_checkpoint();
        let response = structured_tool_success(&pending, result.clone());
        let next = runtime
            .apply(
                response.clone(),
                running(turn_id)
                    .dispatch(llm_call(1))
                    .observe_tool_result(&pending, &response)
                    .observe_tool_execution_finished("program", run_typescript_success(result)),
            )
            .only_action();

        // Assert
        assert_eq!(pending["call"]["name"], format!("agents.{operation}"));
        assert_eq!(pending["call"]["arguments"], arguments);
        runtime.finish_turn_with_text(turn_id, &next, "done");
    }
}

/// *Prepare*: A model calls a retired agent spelling, including destructive stop/close.
/// *Do*: Evaluate the call in a fresh program and catch its migration diagnostic.
/// *Assert*: Only the next model completion is dispatched; no Runtime effect exists.
#[test]
fn retired_agent_calls_fail_before_runtime_dispatch() {
    for expression in [
        "tools.agent.close({name:'child'})",
        "tools.subagent.stop({name:'child'})",
        "tools.collaboration.spawn_agent({name:'child',message:'work'})",
    ] {
        // Prepare
        let mut runtime = SynchronousRuntime::new(config());
        let first = runtime.start_turn("deprecated", "coordinate");
        let source =
            format!("try {{ await {expression}; }} catch (error) {{ return error.name; }}");

        // Do / Assert
        let next = runtime
            .apply(
                run_typescript_completion(action_id(&first), "program", &source),
                running("deprecated")
                    .dispatch(llm_call(1))
                    .observe(tool_execution_started("deprecated", "program"))
                    .observe_tool_execution_finished(
                        "program",
                        run_typescript_success(json!("deprecated_tool")),
                    ),
            )
            .only_action();
        runtime.finish_turn_with_text("deprecated", &next, "corrected");
    }
}

/// *Prepare*: A host enables spawn/list and supplies private authoring metadata.
/// *Do*: Start an ordinary Turn through the serialized protocol.
/// *Assert*: Static model context names agents without rendering catalog paths or descriptions.
#[test]
fn agent_metadata_does_not_change_model_context() {
    // Prepare
    let mut settings = config();
    settings.settings.tools.subagents = serde_json::from_value(json!({
        "mode": "enabled", "operations": ["list", "spawn"],
    }))
    .unwrap();
    settings.capabilities.agent_types = serde_json::from_value(json!([{
        "name": "private-agent", "description": "private-description", "path": "/authoring/private.toml",
    }])).unwrap();
    let mut runtime = SynchronousRuntime::new(settings);

    // Do
    let first = runtime.start_turn("static-context", "coordinate");

    // Assert
    let context = first["model_input"].to_string();
    assert!(context.contains("tools.agents"));
    assert!(!context.contains("private-agent"));
    assert!(!context.contains("private-description"));
    assert!(!context.contains("private.toml"));
    runtime.finish_turn_with_text("static-context", &first, "done");
}

/// *Prepare*: A pre-tool hook selects the canonical agents.spawn Runtime identity.
/// *Do*: Restore the pending hook, approve changed arguments, and complete the spawn.
/// *Assert*: The hook and Runtime Action use the same canonical identity and approved arguments.
#[test]
fn agent_hooks_select_the_canonical_runtime_identity() {
    // Prepare
    let turn_id = "agent-hook";
    let mut settings = config();
    settings.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": "spawn-pre", "point": "pre_tool_call", "order": 0,
        "selector": {"type": "tool_keys", "tool_keys": [{
            "target": "subagent", "qualified_name": "agents.spawn",
        }]},
    }]))
    .unwrap();
    let mut runtime = SynchronousRuntime::new(settings);
    let first = runtime.start_turn(turn_id, "delegate");
    let hook = runtime
        .complete_with_typescript_program(
            turn_id,
            &first,
            "program",
            "async function main() { return tools.agents.spawn({name:'child', message:'work'}); }",
            [hook_call("pre_tool_call", &["spawn-pre"], Value::Null)],
        )
        .only_action();
    let approved = json!({"name": "reviewer", "message": "review the patch"});

    // Do
    runtime.restart_from_checkpoint();
    let spawn = runtime
        .apply(
            hook_completed(
                &hook,
                "pre_tool_call",
                json!({
                    "type": "continue", "effective_arguments": approved,
                }),
            ),
            running(turn_id).dispatch(runtime_tool("agents.spawn", approved.clone())),
        )
        .only_action();
    let result = json!({"type": "success"});
    let response = structured_tool_success(&spawn, result.clone());
    let next = runtime
        .apply(
            response.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&spawn, &response)
                .observe_tool_execution_finished("program", run_typescript_success(result)),
        )
        .only_action();

    // Assert
    assert_eq!(hook["input"]["tool_call"]["call"]["name"], "agents.spawn");
    assert_eq!(spawn["call"]["arguments"], approved);
    runtime.finish_turn_with_text(turn_id, &next, "done");
}
