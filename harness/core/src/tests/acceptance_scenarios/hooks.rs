use super::*;

///
/// *Prepare*: One session configures pre-agent, pre-LLM, post-LLM, pre-tool, post-tool, and post-agent hooks.
/// *Do*: Drive a tool-using turn through every hook and a final model completion.
/// *Assert*: Every hook transition, delayed observation, transformed result, and turn state appears in lifecycle order.
///
#[test]
fn all_hook_points_execute_in_one_tool_using_turn() {
    // Prepare
    let turn_id = "turn-all-hooks";
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([
        {"id": "all-pre-agent", "point": "pre_agent_turn", "order": 0, "selector": {"type": "always"}},
        {"id": "all-pre-llm", "point": "pre_llm_call", "order": 1, "selector": {"type": "always"}},
        {"id": "all-post-llm", "point": "post_llm_call", "order": 2, "selector": {"type": "always"}},
        {"id": "all-pre-tool", "point": "pre_tool_call", "order": 3, "selector": {"type": "always"}},
        {"id": "all-post-tool", "point": "post_tool_call", "order": 4, "selector": {"type": "always"}},
        {"id": "all-post-agent", "point": "post_agent_turn", "order": 5, "selector": {"type": "always"}}
    ]))
    .expect("all hook points configure");
    let mut runtime = SynchronousRuntime::new(config);
    let mut visited = Vec::new();

    // Do
    let pre_agent = runtime
        .apply(
            user_message(turn_id, "read the hooked file", "queue"),
            running(turn_id).dispatch(hook_call(
                "pre_agent_turn",
                &["all-pre-agent"],
                json!({
                    "user_content": [{"type": "text", "text": "read the hooked file"}],
                }),
            )),
        )
        .only_action();
    visited.push(pre_agent["hook"].as_str().unwrap().to_string());
    let pre_llm = runtime
        .apply(
            hook_completed(
                &pre_agent,
                "pre_agent_turn",
                json!({
                    "type": "continue",
                    "user_content": [{"type": "text", "text": "read the hooked file"}],
                }),
            ),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &["all-pre-llm"], Value::Null))
                .observe(turn_started(turn_id, "read the hooked file")),
        )
        .only_action();
    visited.push(pre_llm["hook"].as_str().unwrap().to_string());
    let first_completion = runtime
        .apply(
            hook_completed(&pre_llm, "pre_llm_call", json!({"type": "continue"})),
            running(turn_id).dispatch(llm_call(0)),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &first_completion,
        "replace",
        &[model_system(), model_user_text("read the hooked file")],
    );
    let tool_calls = vec![tool_call(
        "all-hooks-read",
        "read_file",
        json!({"path": "original.txt"}),
    )];
    let post_llm = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), tool_calls.clone()),
            running(turn_id).dispatch(hook_call(
                "post_llm_call",
                &["all-post-llm"],
                json!({"candidate": tool_calls_candidate(&tool_calls)}),
            )),
        )
        .only_action();
    visited.push(post_llm["hook"].as_str().unwrap().to_string());
    let pre_tool = runtime
        .apply(
            hook_completed(
                &post_llm,
                "post_llm_call",
                json!({"type": "accept", "acceptance": {"type": "candidate"}}),
            ),
            running(turn_id)
                .dispatch(hook_call(
                    "pre_tool_call",
                    &["all-pre-tool"],
                    json!({
                        "tool_call": {
                            "call_id": "all-hooks-read",
                            "call": {
                                "type": "runtime_builtin",
                                "name": "file_system.read_file",
                                "arguments": {"path": "original.txt"},
                            },
                        },
                    }),
                ))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &tool_calls,
                ))
                .observe(tool_execution_started(turn_id, "all-hooks-read")),
        )
        .only_action();
    visited.push(pre_tool["hook"].as_str().unwrap().to_string());
    let tool_action = runtime
        .apply(
            hook_completed(
                &pre_tool,
                "pre_tool_call",
                json!({"type": "continue", "effective_arguments": {"path": "effective.txt"}}),
            ),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "effective.txt"}),
            )),
        )
        .only_action();
    let raw_tool_result = text_tool_success(&tool_action, "raw tool result");
    let post_tool = runtime
        .apply(
            raw_tool_result.clone(),
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["all-post-tool"],
                json!({
                    "tool_call": {
                        "call_id": "all-hooks-read",
                        "call": {
                            "type": "runtime_builtin",
                            "name": "file_system.read_file",
                            "arguments": {"path": "effective.txt"},
                        },
                    },
                    "tool_result": raw_tool_result["result"],
                }),
            )),
        )
        .only_action();
    visited.push(post_tool["hook"].as_str().unwrap().to_string());
    let hooked_result = json!({
        "type": "success",
        "content": [{"type": "text", "text": "hooked tool result"}],
    });
    let second_pre_llm = runtime
        .apply(
            hook_completed(
                &post_tool,
                "post_tool_call",
                json!({"tool_result": hooked_result}),
            ),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &["all-pre-llm"], Value::Null))
                .observe(tool_result_committed(
                    turn_id,
                    &tool_action,
                    hooked_result.clone(),
                ))
                .observe(tool_execution_finished(
                    turn_id,
                    "all-hooks-read",
                    hooked_result,
                )),
        )
        .only_action();
    visited.push(second_pre_llm["hook"].as_str().unwrap().to_string());
    let final_completion = runtime
        .apply(
            hook_completed(&second_pre_llm, "pre_llm_call", json!({"type": "continue"})),
            running(turn_id).dispatch(llm_call(1)),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &final_completion,
        "append",
        &[
            model_assistant_tool_calls(&tool_calls),
            model_tool_text("all-hooks-read", "read_file", "hooked tool result"),
        ],
    );
    let final_post_llm = runtime
        .apply(
            text_completion(&final_completion, "hooked answer"),
            running(turn_id).dispatch(hook_call(
                "post_llm_call",
                &["all-post-llm"],
                json!({"candidate": text_candidate("hooked answer")}),
            )),
        )
        .only_action();
    visited.push(final_post_llm["hook"].as_str().unwrap().to_string());
    let post_agent = runtime
        .apply(
            hook_completed(
                &final_post_llm,
                "post_llm_call",
                json!({"type": "accept", "acceptance": {"type": "candidate"}}),
            ),
            running(turn_id).dispatch(hook_call(
                "post_agent_turn",
                &["all-post-agent"],
                json!({"candidate": text_candidate("hooked answer")}),
            )),
        )
        .only_action();
    visited.push(post_agent["hook"].as_str().unwrap().to_string());
    runtime.apply(
        hook_completed(
            &post_agent,
            "post_agent_turn",
            json!({"type": "accept", "acceptance": {"type": "candidate"}}),
        ),
        completed(
            turn_id,
            vec![json!({"type": "text", "text": "hooked answer"})],
        )
        .observe(assistant_text_committed(
            turn_id,
            &final_completion,
            "hooked answer",
        ))
        .observe(turn_completed(turn_id, "hooked answer")),
    );

    // Assert
    assert_eq!(
        visited,
        [
            "pre_agent_turn",
            "pre_llm_call",
            "post_llm_call",
            "pre_tool_call",
            "post_tool_call",
            "pre_llm_call",
            "post_llm_call",
            "post_agent_turn",
        ]
    );
    assert_eq!(tool_action["call"]["arguments"]["path"], "effective.txt");
}

///
/// *Prepare*: An always binding and a filesystem-specific binding both observe the same direct read call.
/// *Do*: Run both Runtime hook handlers in order, return their final transformed arguments, execute the tool, and finish the turn.
/// *Assert*: The second handler receives the first handler's output and every Core transition has the expected observation and turn state.
///
#[test]
fn multiple_matching_hooks_observe_the_same_tool_in_binding_order() {
    // Prepare
    let turn_id = "turn-multiple-hooks";
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([
        {"id": "observe-all-tools", "point": "pre_tool_call", "order": 10, "selector": {"type": "always"}},
        {
            "id": "observe-file-reads",
            "point": "pre_tool_call",
            "order": 20,
            "selector": {
                "type": "tool_keys",
                "tool_keys": [{"target": "filesystem", "qualified_name": "file_system.read_file"}]
            }
        }
    ]))
    .expect("multiple hook bindings configure");
    let mut runtime = SynchronousRuntime::new(config);
    let first_completion = runtime.start_turn(turn_id, "read the observed file");
    let calls = vec![tool_call(
        "multiple-hooks-read",
        "read_file",
        json!({"path": "observed.txt"}),
    )];
    let hook_action = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(hook_call(
                    "pre_tool_call",
                    &["observe-all-tools", "observe-file-reads"],
                    json!({
                        "tool_call": {
                            "call_id": "multiple-hooks-read",
                            "call": {
                                "type": "runtime_builtin",
                                "name": "file_system.read_file",
                                "arguments": {"path": "observed.txt"},
                            },
                        },
                    }),
                ))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(turn_id, "multiple-hooks-read")),
        )
        .only_action();

    // Do
    let mut effective_arguments = hook_action["input"]["tool_call"]["call"]["arguments"].clone();
    let mut executed_bindings = Vec::new();
    for binding_id in hook_action["hook_binding_ids"]
        .as_array()
        .expect("pre-tool hook has binding IDs")
    {
        let binding_id = binding_id.as_str().expect("hook binding ID is a string");
        executed_bindings.push(binding_id.to_string());
        effective_arguments = match binding_id {
            "observe-all-tools" => {
                assert_eq!(effective_arguments, json!({"path": "observed.txt"}));
                json!({"path": "after-first-hook.txt"})
            }
            "observe-file-reads" => {
                assert_eq!(effective_arguments, json!({"path": "after-first-hook.txt"}));
                json!({"path": "after-second-hook.txt"})
            }
            other => panic!("unexpected hook binding {other:?}"),
        };
    }
    let tool_action = runtime
        .apply(
            hook_completed(
                &hook_action,
                "pre_tool_call",
                json!({"type": "continue", "effective_arguments": effective_arguments}),
            ),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "after-second-hook.txt"}),
            )),
        )
        .only_action();
    let tool_result = text_tool_success(&tool_action, "observed contents");
    let final_completion = runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&tool_action, &tool_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &final_completion,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_tool_text("multiple-hooks-read", "read_file", "observed contents"),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &final_completion, "observers completed");

    // Assert
    assert_eq!(hook_action["hook"], "pre_tool_call");
    assert_eq!(
        executed_bindings,
        ["observe-all-tools", "observe-file-reads"]
    );
    assert_eq!(
        tool_action["call"]["arguments"],
        json!({"path": "after-second-hook.txt"})
    );
}

///
/// *Prepare*: One turn waits on a pre-LLM hook. Another turn waits on a post-LLM hook with an uncommitted assistant candidate.
/// *Do*: Interrupt each turn while its hook Action is pending.
/// *Assert*: Both hooks are abandoned, but only the post-LLM case discards an assistant candidate.
///
#[test]
fn interrupting_hooks_discards_only_an_existing_completion_candidate() {
    // Prepare: pre-LLM hook
    let pre_turn_id = "turn-interrupt-pre-llm";
    let mut pre_config = config();
    pre_config.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": "interrupt-pre-llm",
        "point": "pre_llm_call",
        "order": 0,
        "selector": {"type": "always"},
    }]))
    .expect("pre-LLM hook config deserializes");
    let mut pre_runtime = SynchronousRuntime::new(pre_config);
    let pre_hook = pre_runtime
        .apply(
            user_message(pre_turn_id, "stop before the model call", "queue"),
            running(pre_turn_id)
                .dispatch(hook_call(
                    "pre_llm_call",
                    &["interrupt-pre-llm"],
                    Value::Null,
                ))
                .observe(turn_started(pre_turn_id, "stop before the model call")),
        )
        .only_action();

    // Do: interrupt the pre-LLM hook
    let pre_reason = "stop during the pre-LLM hook";
    pre_runtime.apply(
        json!({
            "type": "interrupt",
            "expected_turn_id": pre_turn_id,
            "reason": pre_reason,
        }),
        interrupted(pre_turn_id, pre_reason)
            .observe(turn_interrupted(pre_turn_id, pre_reason))
            .observe(action_abandoned(pre_turn_id, &pre_hook, "interrupt")),
    );

    // Prepare: post-LLM hook
    let post_turn_id = "turn-interrupt-post-llm";
    let mut post_config = config();
    post_config.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": "interrupt-post-llm",
        "point": "post_llm_call",
        "order": 0,
        "selector": {"type": "always"},
    }]))
    .expect("post-LLM hook config deserializes");
    let mut post_runtime = SynchronousRuntime::new(post_config);
    let completion = post_runtime.start_turn(post_turn_id, "stop after the model response");
    let post_hook = post_runtime
        .apply(
            text_completion(&completion, "uncommitted answer"),
            running(post_turn_id).dispatch(hook_call(
                "post_llm_call",
                &["interrupt-post-llm"],
                json!({"candidate": text_candidate("uncommitted answer")}),
            )),
        )
        .only_action();

    // Do: interrupt the post-LLM hook
    let post_reason = "stop during the post-LLM hook";
    post_runtime.apply(
        json!({
            "type": "interrupt",
            "expected_turn_id": post_turn_id,
            "reason": post_reason,
        }),
        interrupted(post_turn_id, post_reason)
            .observe(agent_completion_candidate_discarded(
                post_turn_id,
                &completion,
                "interrupt",
            ))
            .observe(turn_interrupted(post_turn_id, post_reason))
            .observe(action_abandoned(post_turn_id, &post_hook, "interrupt")),
    );

    // Assert
    assert_eq!(pre_hook["hook"], "pre_llm_call");
    assert_eq!(post_hook["hook"], "post_llm_call");
}
