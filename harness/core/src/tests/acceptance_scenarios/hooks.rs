use super::*;

#[test]
fn rejects_new_turn_when_pre_agent_or_pre_llm_hook_is_pending() {
    for point in ["pre_agent_turn", "pre_llm_call"] {
        let turn_id = "turn-hook-busy";
        let mut config = config();
        config.capabilities.hook_bindings = serde_json::from_value(json!([{
            "id": "pending-hook",
            "point": point,
            "order": 0,
            "selector": {"type": "always"},
        }]))
        .unwrap();
        let mut runtime = SynchronousRuntime::new(config);
        let input = if point == "pre_agent_turn" {
            json!({"user_content": [{"type": "text", "text": "first request"}]})
        } else {
            Value::Null
        };
        let mut expected = running(turn_id).dispatch(hook_call(point, &["pending-hook"], input));

        if point == "pre_llm_call" {
            expected = expected.observe(turn_started(turn_id, "first request"));
        }
        let hook = runtime
            .apply(user_message(turn_id, "first request", "queue"), expected)
            .only_action();

        runtime.reject(
            user_message("turn-follow-up", "next request", "queue"),
            json!({
                "code": "invalid_state",
                "command_type": "user_message",
                "state": "running",
            }),
        );

        let mut output = json!({"type": "continue"});
        let mut expected = running(turn_id).dispatch(llm_call(0));

        if point == "pre_agent_turn" {
            output["user_content"] = json!([{"type": "text", "text": "first request"}]);
            expected = expected.observe(turn_started(turn_id, "first request"));
        }
        let completion = runtime
            .apply(hook_completed(&hook, point, output), expected)
            .only_action();

        runtime.assert_last_model_message_update(
            &completion,
            "replace",
            &[model_system(), model_user_text("first request")],
        );
        runtime.finish_turn_with_text(turn_id, &completion, "first answer");
    }
}

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
/// *Prepare*: One session binds a post-LLM hook and a post-agent hook.
/// *Do*: Complete the model call with every provider token counter, then accept the candidate at each hook.
/// *Assert*: Both hooks and the committed message receive every token counter unchanged.
///
#[test]
fn completion_hooks_receive_every_token_counter() {
    // Prepare
    let turn_id = "turn-hook-usage";
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([
        {"id": "usage-post-llm", "point": "post_llm_call", "order": 0, "selector": {"type": "always"}},
        {"id": "usage-post-agent", "point": "post_agent_turn", "order": 1, "selector": {"type": "always"}}
    ]))
    .expect("completion hook bindings configure");
    let mut runtime = SynchronousRuntime::new(config);
    let completion = runtime.start_turn(turn_id, "count my tokens");
    let usage = json!({
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "cached_input_tokens": 80,
        "cache_write_tokens": 25,
        "reasoning_tokens": 12,
    });
    let mut candidate = text_candidate("counted");
    candidate["usage"] = usage.clone();
    let mut model_result = text_completion(&completion, "counted");
    model_result["result"]["usage"] = usage;

    // Do
    let post_llm = runtime
        .apply(
            model_result,
            running(turn_id).dispatch(hook_call(
                "post_llm_call",
                &["usage-post-llm"],
                json!({"candidate": candidate}),
            )),
        )
        .only_action();
    let post_agent = runtime
        .apply(
            hook_completed(
                &post_llm,
                "post_llm_call",
                json!({"type": "accept", "acceptance": {"type": "candidate"}}),
            ),
            running(turn_id).dispatch(hook_call(
                "post_agent_turn",
                &["usage-post-agent"],
                json!({"candidate": candidate}),
            )),
        )
        .only_action();
    runtime.apply(
        hook_completed(
            &post_agent,
            "post_agent_turn",
            json!({"type": "accept", "acceptance": {"type": "candidate"}}),
        ),
        completed(turn_id, vec![json!({"type": "text", "text": "counted"})])
            .observe(json!({
                "type": "assistant_message_committed",
                "turn_id": turn_id,
                "action_id": action_id(&completion),
                "candidate": candidate,
            }))
            .observe(turn_completed(turn_id, "counted")),
    );

    // Assert
    assert_eq!(
        post_llm["input"]["candidate"]["usage"]["cache_write_tokens"],
        25
    );
    assert_eq!(
        post_agent["input"]["candidate"]["usage"]["reasoning_tokens"],
        12
    );
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

///
/// *Prepare*: A pre-agent hook is bound and a queued turn waits on it.
/// *Do*: Skip the turn, send a late interrupt for it, then queue and run another turn.
/// *Assert*: The skip starts and completes the turn with the hook's reason, the late interrupt changes nothing, and the next turn's model input omits the skipped request.
///
#[test]
fn pre_agent_skip_completes_the_turn_and_the_next_turn_runs() {
    // Prepare
    let skipped_turn_id = "turn-skip";
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": "skip-pre-agent",
        "point": "pre_agent_turn",
        "order": 0,
        "selector": {"type": "always"},
    }]))
    .expect("pre-agent hook config deserializes");
    let mut runtime = SynchronousRuntime::new(config);
    let skip_hook = runtime
        .apply(
            user_message(skipped_turn_id, "skip this request", "queue"),
            running(skipped_turn_id)
                .dispatch(pre_agent_hook_call("skip-pre-agent", "skip this request")),
        )
        .only_action();
    let reason = vec![json!({"type": "text", "text": "disabled"})];

    // Do
    runtime.apply(
        hook_completed(
            &skip_hook,
            "pre_agent_turn",
            json!({"type": "skip", "reason": reason.clone()}),
        ),
        completed(skipped_turn_id, reason.clone())
            .observe(turn_started(skipped_turn_id, "skip this request"))
            .observe(turn_completed_skipped(skipped_turn_id, reason.clone())),
    );
    runtime.apply(
        json!({
            "type": "interrupt",
            "expected_turn_id": skipped_turn_id,
            "reason": "late stop",
        }),
        completed(skipped_turn_id, reason),
    );
    let next_turn_id = "turn-next";
    let next_hook = runtime
        .apply(
            user_message(next_turn_id, "continue", "queue"),
            running(next_turn_id).dispatch(pre_agent_hook_call("skip-pre-agent", "continue")),
        )
        .only_action();
    let next_completion = runtime
        .apply(
            hook_completed(
                &next_hook,
                "pre_agent_turn",
                json!({
                    "type": "continue",
                    "user_content": [{"type": "text", "text": "continue"}],
                }),
            ),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "continue")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &next_completion,
        "replace",
        &[model_system(), model_user_text("continue")],
    );
    runtime.finish_turn_with_text(next_turn_id, &next_completion, "done");
}

///
/// *Prepare*: A pre-LLM hook is bound. The turn's first model call answers with text and a tool
/// call, the tool succeeds, and the pre-LLM hook of the second model call is pending.
/// *Do*: Skip the second model call from the hook with a reason.
/// *Assert*: The held completion is discarded as skipped and the turn completes on the stop reason
/// `skipped` with the reason as its output, not the first call's text, with no further Action.
///
#[test]
fn pre_llm_skip_after_a_tool_call_completes_the_turn_with_the_reason() {
    // Prepare
    let turn_id = "turn-pre-llm-skip-later";
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": "skip-pre-llm",
        "point": "pre_llm_call",
        "order": 0,
        "selector": {"type": "always"},
    }]))
    .expect("pre-LLM hook config deserializes");
    let mut runtime = SynchronousRuntime::new(config);
    let first_hook = runtime
        .apply(
            user_message(turn_id, "read the notes", "queue"),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &["skip-pre-llm"], Value::Null))
                .observe(turn_started(turn_id, "read the notes")),
        )
        .only_action();
    let first_completion = runtime
        .apply(
            hook_completed(&first_hook, "pre_llm_call", json!({"type": "continue"})),
            running(turn_id).dispatch(llm_call(0)),
        )
        .only_action();
    let calls = vec![tool_call(
        "call-read",
        "read_file",
        json!({"path": "notes.txt"}),
    )];
    let first_text = json!({"type": "text", "text": "Let me read the notes first."});
    let mut parts = vec![first_text.clone()];
    parts.extend(calls.iter().cloned());
    let mut committed = assistant_tool_calls_committed(turn_id, &first_completion, &calls);
    committed["candidate"]["message"]["content"]
        .as_array_mut()
        .expect("candidate content is an array")
        .insert(0, first_text);
    let tool_action = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), parts),
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "notes.txt"}),
                ))
                .observe(committed)
                .observe(tool_execution_started(turn_id, "call-read")),
        )
        .only_action();
    let tool_result = text_tool_success(&tool_action, "file contents");
    let second_hook = runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &["skip-pre-llm"], Value::Null))
                .observe_completed_tool_result(&tool_action, &tool_result),
        )
        .only_action();
    let reason = vec![json!({"type": "text", "text": "model budget spent"})];
    let skip = hook_completed(
        &second_hook,
        "pre_llm_call",
        json!({"type": "skip", "reason": reason.clone()}),
    );
    // The held completion's ID is never dispatched, so the scenario reads it from Core.
    let held_completion = json!({
        "action_id": runtime.preview_observations(skip.clone())[0]["action_id"],
    });

    // Do
    let skipped = runtime.apply(
        skip,
        completed(turn_id, reason.clone())
            .observe(agent_completion_candidate_discarded(
                turn_id,
                &held_completion,
                "skipped",
            ))
            .observe(turn_completed_skipped(turn_id, reason)),
    );

    // Assert
    assert!(skipped.actions().is_empty());
    assert_ne!(action_id(&held_completion), action_id(&first_completion));
}

///
/// *Prepare*: A pre-agent hook is bound and pending for a new turn.
/// *Do*: Send a context message while the hook runs, then continue the turn from the hook.
/// *Assert*: Core keeps the hook Action, and the first model call reads the context before the
/// user message.
///
#[test]
fn context_sent_during_the_pre_agent_hook_precedes_the_user_message() {
    // Prepare
    let turn_id = "turn-pre-agent-context";
    let mut runtime = SynchronousRuntime::new(hook_config("context-pre-agent", "pre_agent_turn"));
    let hook = runtime
        .apply(
            user_message(turn_id, "push the fix", "queue"),
            running(turn_id).dispatch(pre_agent_hook_call("context-pre-agent", "push the fix")),
        )
        .only_action();

    // Do
    runtime.apply(
        context_message("GitHub status: not connected."),
        running(turn_id).keep(&hook),
    );
    let completion = runtime
        .apply(
            hook_completed(
                &hook,
                "pre_agent_turn",
                json!({
                    "type": "continue",
                    "user_content": [{"type": "text", "text": "push the fix"}],
                }),
            ),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "push the fix")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &completion,
        "replace",
        &[
            model_system(),
            model_context_text("GitHub status: not connected."),
            model_user_text("push the fix"),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &completion, "done");
}

///
/// *Prepare*: A pre-agent hook is bound and pending for a new turn.
/// *Do*: Send a notification, then a context message while the hook runs, restart Core from its
/// checkpoint, then continue the turn from the hook.
/// *Assert*: The first model call reads the notification, then the context, then the user
/// message.
///
#[test]
fn context_sent_during_the_pre_agent_hook_after_a_notification_precedes_the_user_message() {
    // Prepare
    let turn_id = "turn-pre-agent-notification-context";
    let mut runtime = SynchronousRuntime::new(hook_config("context-pre-agent", "pre_agent_turn"));
    let hook = runtime
        .apply(
            user_message(turn_id, "push the fix", "queue"),
            running(turn_id).dispatch(pre_agent_hook_call("context-pre-agent", "push the fix")),
        )
        .only_action();
    let notification = notification("notification-during-hook", "github was disconnected");

    // Do
    runtime.receive_notification(turn_id, &notification, &[&hook]);
    runtime.apply(
        context_message("GitHub status: not connected."),
        running(turn_id).keep(&hook),
    );
    runtime.restart_from_checkpoint();
    let completion = runtime
        .apply(
            hook_completed(
                &hook,
                "pre_agent_turn",
                json!({
                    "type": "continue",
                    "user_content": [{"type": "text", "text": "push the fix"}],
                }),
            ),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "push the fix"))
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&notification),
                )),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &completion,
        "replace",
        &[
            model_system(),
            model_notifications(&[&notification]),
            model_context_text("GitHub status: not connected."),
            model_user_text("push the fix"),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &completion, "done");
}

///
/// *Prepare*: A pre-LLM hook is bound and pending before a turn's first model call.
/// *Do*: Send a context message while the hook runs, restart Core from its checkpoint, then
/// continue from the hook.
/// *Assert*: The context survives the restart and the model call reads it after the user message.
///
#[test]
fn context_sent_during_the_pre_llm_hook_reaches_the_next_model_call_after_a_restart() {
    // Prepare
    let turn_id = "turn-pre-llm-context";
    let mut runtime = SynchronousRuntime::new(hook_config("context-pre-llm", "pre_llm_call"));
    let hook = runtime
        .apply(
            user_message(turn_id, "clone the repository", "queue"),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &["context-pre-llm"], Value::Null))
                .observe(turn_started(turn_id, "clone the repository")),
        )
        .only_action();

    // Do
    runtime.apply(
        context_message("The clone of acme/api failed."),
        running(turn_id).keep(&hook),
    );
    runtime.restart_from_checkpoint();
    let completion = runtime
        .apply(
            hook_completed(&hook, "pre_llm_call", json!({"type": "continue"})),
            running(turn_id).dispatch(llm_call(0)),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &completion,
        "replace",
        &[
            model_system(),
            model_user_text("clone the repository"),
            model_context_text("The clone of acme/api failed."),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &completion, "done");
}

///
/// *Prepare*: A pre-LLM hook is pending with no context received.
/// *Do*: Checkpoint, assert the checkpoint omits `pending_context`, restore it, then continue.
/// *Assert*: A checkpoint written without the field restores, and the turn runs as before.
///
#[test]
fn an_active_checkpoint_without_pending_context_restores() {
    // Prepare
    let turn_id = "turn-no-pending-context";
    let mut runtime = SynchronousRuntime::new(hook_config("no-context", "pre_llm_call"));
    let hook = runtime
        .apply(
            user_message(turn_id, "work", "queue"),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &["no-context"], Value::Null))
                .observe(turn_started(turn_id, "work")),
        )
        .only_action();
    let checkpoint = runtime.checkpoint_value();

    // Do
    runtime.restore_checkpoint_value(
        checkpoint.clone(),
        hook_config("no-context", "pre_llm_call"),
    );
    let completion = runtime
        .apply(
            hook_completed(&hook, "pre_llm_call", json!({"type": "continue"})),
            running(turn_id).dispatch(llm_call(0)),
        )
        .only_action();

    // Assert
    assert!(checkpoint["turn"].get("pending_context").is_none());
    runtime.assert_last_model_message_update(
        &completion,
        "replace",
        &[model_system(), model_user_text("work")],
    );
}

///
/// *Prepare*: A pre-agent hook is bound and pending for a new turn, and it sent a context message.
/// *Do*: Skip the turn from the hook, then start another turn.
/// *Assert*: The skipped request never reaches the model, but the context precedes the next
/// turn's message.
///
#[test]
fn context_sent_by_a_skipping_pre_agent_hook_reaches_the_next_turn() {
    // Prepare
    let skipped_turn_id = "turn-skipped-with-context";
    let mut runtime = SynchronousRuntime::new(hook_config("context-skip", "pre_agent_turn"));
    let hook = runtime
        .apply(
            user_message(skipped_turn_id, "deploy", "queue"),
            running(skipped_turn_id).dispatch(pre_agent_hook_call("context-skip", "deploy")),
        )
        .only_action();
    runtime.apply(
        context_message("Deployments are frozen."),
        running(skipped_turn_id).keep(&hook),
    );
    let reason = vec![json!({"type": "text", "text": "frozen"})];

    // Do
    runtime.apply(
        hook_completed(
            &hook,
            "pre_agent_turn",
            json!({"type": "skip", "reason": reason.clone()}),
        ),
        completed(skipped_turn_id, reason.clone())
            .observe(turn_started(skipped_turn_id, "deploy"))
            .observe(turn_completed_skipped(skipped_turn_id, reason)),
    );
    let next_turn_id = "turn-after-skip";
    let next_hook = runtime
        .apply(
            user_message(next_turn_id, "status?", "queue"),
            running(next_turn_id).dispatch(pre_agent_hook_call("context-skip", "status?")),
        )
        .only_action();
    let completion = runtime
        .apply(
            hook_completed(
                &next_hook,
                "pre_agent_turn",
                json!({
                    "type": "continue",
                    "user_content": [{"type": "text", "text": "status?"}],
                }),
            ),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "status?")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &completion,
        "replace",
        &[
            model_system(),
            model_context_text("Deployments are frozen."),
            model_user_text("status?"),
        ],
    );
}

///
/// *Prepare*: A skipping pre-agent hook left a context message waiting for the next turn.
/// *Do*: Start the next turn, send newer context from its pre-agent hook, then continue it.
/// *Assert*: The model reads the waiting context first and the newer context right before the
/// user message.
///
#[test]
fn context_from_a_pre_agent_hook_follows_context_waiting_for_its_turn() {
    // Prepare
    let skipped_turn_id = "turn-skipped-before-newer-context";
    let mut runtime = SynchronousRuntime::new(hook_config("context-newer", "pre_agent_turn"));
    let hook = runtime
        .apply(
            user_message(skipped_turn_id, "deploy", "queue"),
            running(skipped_turn_id).dispatch(pre_agent_hook_call("context-newer", "deploy")),
        )
        .only_action();
    runtime.apply(
        context_message("Deployments are frozen."),
        running(skipped_turn_id).keep(&hook),
    );
    let reason = vec![json!({"type": "text", "text": "frozen"})];
    runtime.apply(
        hook_completed(
            &hook,
            "pre_agent_turn",
            json!({"type": "skip", "reason": reason.clone()}),
        ),
        completed(skipped_turn_id, reason.clone())
            .observe(turn_started(skipped_turn_id, "deploy"))
            .observe(turn_completed_skipped(skipped_turn_id, reason)),
    );

    // Do
    let next_turn_id = "turn-with-newer-context";
    let next_hook = runtime
        .apply(
            user_message(next_turn_id, "deploy now", "queue"),
            running(next_turn_id).dispatch(pre_agent_hook_call("context-newer", "deploy now")),
        )
        .only_action();
    runtime.apply(
        context_message("Deployments are open again."),
        running(next_turn_id).keep(&next_hook),
    );
    let completion = runtime
        .apply(
            hook_completed(
                &next_hook,
                "pre_agent_turn",
                json!({
                    "type": "continue",
                    "user_content": [{"type": "text", "text": "deploy now"}],
                }),
            ),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "deploy now")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &completion,
        "replace",
        &[
            model_system(),
            model_context_text("Deployments are frozen."),
            model_context_text("Deployments are open again."),
            model_user_text("deploy now"),
        ],
    );
}

fn hook_config(binding_id: &str, point: &str) -> HarnessConfig {
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": binding_id,
        "point": point,
        "order": 0,
        "selector": {"type": "always"},
    }]))
    .expect("hook config deserializes");
    config
}

fn context_message(text: &str) -> Value {
    json!({
        "type": "context_message",
        "content": [{"type": "text", "text": text}],
    })
}

fn pre_agent_hook_call(binding_id: &str, text: &str) -> Value {
    hook_call(
        "pre_agent_turn",
        &[binding_id],
        json!({"user_content": [{"type": "text", "text": text}]}),
    )
}

///
/// *Prepare*: A pre-LLM hook is bound, and the first completion is cut off at the output limit.
/// *Do*: Send context while the retry's pre-LLM hook is pending, then complete the hook.
/// *Assert*: The retried model call reads the context after the retry feedback.
///
#[test]
fn context_sent_during_a_retry_pre_llm_hook_reaches_the_retried_model_call() {
    // Prepare
    let turn_id = "turn-retry-context";
    let mut config = hook_config("context-retry", "pre_llm_call");
    config.settings.turn = serde_json::from_value(json!({
        "max_iterations": 8,
        "output_limit_recovery": {"mode": "retry", "max_retries": 2},
    }))
    .expect("turn settings deserialize");
    let mut runtime = SynchronousRuntime::new(config);
    let first_hook = runtime
        .apply(
            user_message(turn_id, "write the report", "queue"),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &["context-retry"], Value::Null))
                .observe(turn_started(turn_id, "write the report")),
        )
        .only_action();
    let first = runtime
        .apply(
            hook_completed(&first_hook, "pre_llm_call", json!({"type": "continue"})),
            running(turn_id).dispatch(llm_call(0)),
        )
        .only_action();
    let retry_hook = runtime
        .apply(
            json!({
                "type": "completion_succeeded",
                "action_id": action_id(&first),
                "result": {
                    "parts": [{"type": "text", "text": "first fragment"}],
                    "finish_reason": "length",
                    "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0},
                },
            }),
            running(turn_id)
                .dispatch(hook_call("pre_llm_call", &["context-retry"], Value::Null))
                .observe(agent_completion_candidate_discarded(turn_id, &first, "retry")),
        )
        .only_action();

    // Do
    runtime.apply(
        context_message("Write the report to report.md."),
        running(turn_id).keep(&retry_hook),
    );
    let retried = runtime
        .apply(
            hook_completed(&retry_hook, "pre_llm_call", json!({"type": "continue"})),
            running(turn_id).dispatch(llm_call(1)),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &retried,
        "append",
        &[
            model_user_text("Your previous response was discarded because it reached the output token limit after 20 tokens. Give a shorter answer; put long content in a file with a tool call if needed."),
            model_context_text("Write the report to report.md."),
        ],
    );
}
