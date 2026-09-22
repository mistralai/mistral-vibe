use super::*;

fn config_with_background_processes() -> HarnessConfig {
    let mut value = serde_json::to_value(config()).expect("base config serializes");
    value["settings"]["tools"]["background_processes"] = json!({"mode": "enabled"});
    serde_json::from_value(value).expect("background-process config deserializes")
}

fn subagent_notification(id: &str, agent_name: &str, message: &str) -> Value {
    json!({
        "type": "notification",
        "notification": {
            "id": id,
            "source": {
                "type": "subagent",
                "agent_name": agent_name,
                "status": "completed",
            },
            "level": "info",
            "message": message,
            "content": [],
        },
    })
}

fn process_notification(id: &str, process_id: &str, message: &str) -> Value {
    json!({
        "type": "notification",
        "notification": {
            "id": id,
            "source": {
                "type": "background_process",
                "process_id": process_id,
                "status": "completed",
                "exit_code": 0,
            },
            "level": "info",
            "message": message,
            "content": [],
        },
    })
}

///
/// *Prepare*: A model starts one subagent through a programmatic tool call.
/// *Do*: Accept the spawn result, deliver completion notification while a model action is pending, and finish after Core exposes it to the model.
/// *Assert*: The notification retains the pending completion, emits its observation immediately, and causes one follow-up model call before completion.
///
#[test]
fn one_subagent_completion_notification_reaches_the_model() {
    // Prepare
    let turn_id = "turn-one-subagent";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "delegate one investigation");
    let source = "async function main() { return tools.agent.spawn({ agentName: 'researcher', message: 'Investigate the issue' }); }";

    // Do
    let spawn_action = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            "program-one-subagent",
            source,
            [runtime_tool(
                "subagent.spawn",
                json!({
                    "agentName": "researcher",
                    "message": "Investigate the issue",
                }),
            )],
        )
        .only_action();
    let spawn_result = structured_tool_success(&spawn_action, json!({"type": "success"}));
    let outer_result = run_typescript_success(json!({"type": "success"}));
    let waiting_completion = runtime
        .apply(
            spawn_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&spawn_action, &spawn_result)
                .observe_tool_execution_finished("program-one-subagent", outer_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &waiting_completion,
        "append",
        &[
            model_assistant_typescript("program-one-subagent", source),
            model_tool("program-one-subagent", "run_typescript"),
        ],
    );
    let notification = subagent_notification(
        "notification-researcher-completed",
        "researcher",
        "researcher completed with evidence",
    );
    runtime.receive_notification(turn_id, &notification, &[&waiting_completion]);
    let notification_completion = runtime
        .apply(
            text_completion(&waiting_completion, "waiting for delegated work"),
            running(turn_id)
                .dispatch(llm_call(2))
                .observe(assistant_text_committed(
                    turn_id,
                    &waiting_completion,
                    "waiting for delegated work",
                ))
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&notification),
                )),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &notification_completion,
        "append",
        &[
            model_assistant_text("waiting for delegated work"),
            model_notifications(&[&notification]),
        ],
    );
    runtime.finish_turn_with_text(
        turn_id,
        &notification_completion,
        "researcher evidence incorporated",
    );

    // Assert
    assert_eq!(spawn_action["call"]["name"], "subagent.spawn");
    assert_eq!(spawn_action["call"]["arguments"]["agentName"], "researcher");
}

///
/// *Prepare*: A model starts two subagents in parallel and later sends each one a follow-up message.
/// *Do*: Resolve both tool batches, deliver both completion notifications while the model is pending, and complete after the notifications are injected.
/// *Assert*: Every unfinished sibling is retained, both notifications are observed, and the final model cache contains both reports.
///
#[test]
fn parallel_subagents_receive_messages_and_completion_notifications() {
    // Prepare
    let turn_id = "turn-parallel-subagents";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "coordinate two researchers");
    let spawn_source = "async function main() { return Promise.all([tools.agent.spawn({ agentName: 'alpha', message: 'Investigate alpha' }), tools.agent.spawn({ agentName: 'beta', message: 'Investigate beta' })]); }";

    // Do
    let spawn_actions = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            "program-spawn-subagents",
            spawn_source,
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
    let [alpha_spawn, beta_spawn]: [Value; 2] = spawn_actions
        .try_into()
        .expect("two subagents are dispatched");
    let alpha_spawn_result = structured_tool_success(&alpha_spawn, json!({"type": "success"}));
    runtime.apply(
        alpha_spawn_result.clone(),
        running(turn_id)
            .keep(&beta_spawn)
            .observe_tool_result(&alpha_spawn, &alpha_spawn_result),
    );
    let beta_spawn_result = structured_tool_success(&beta_spawn, json!({"type": "success"}));
    let spawn_outer_result = run_typescript_success(json!([
        {"type": "success"},
        {"type": "success"},
    ]));
    let message_completion = runtime
        .apply(
            beta_spawn_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&beta_spawn, &beta_spawn_result)
                .observe_tool_execution_finished("program-spawn-subagents", spawn_outer_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &message_completion,
        "append",
        &[
            model_assistant_typescript("program-spawn-subagents", spawn_source),
            model_tool("program-spawn-subagents", "run_typescript"),
        ],
    );
    let message_source = "async function main() { return Promise.all([tools.agent.sendMessage({ agentName: 'alpha', message: 'Check logs' }), tools.agent.sendMessage({ agentName: 'beta', message: 'Check tests' })]); }";
    let message_actions = runtime
        .complete_with_typescript_program(
            turn_id,
            &message_completion,
            "program-message-subagents",
            message_source,
            [
                runtime_tool(
                    "subagent.send_message",
                    json!({"agentName": "alpha", "message": "Check logs"}),
                ),
                runtime_tool(
                    "subagent.send_message",
                    json!({"agentName": "beta", "message": "Check tests"}),
                ),
            ],
        )
        .actions();
    let [alpha_message, beta_message]: [Value; 2] = message_actions
        .try_into()
        .expect("two subagent messages are dispatched");
    let alpha_message_result = structured_tool_success(&alpha_message, json!({"type": "success"}));
    runtime.apply(
        alpha_message_result.clone(),
        running(turn_id)
            .keep(&beta_message)
            .observe_tool_result(&alpha_message, &alpha_message_result),
    );
    let beta_message_result = structured_tool_success(&beta_message, json!({"type": "success"}));
    let message_outer_result = run_typescript_success(json!([
        {"type": "success"},
        {"type": "success"},
    ]));
    let waiting_completion = runtime
        .apply(
            beta_message_result.clone(),
            running(turn_id)
                .dispatch(llm_call(2))
                .observe_tool_result(&beta_message, &beta_message_result)
                .observe_tool_execution_finished("program-message-subagents", message_outer_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &waiting_completion,
        "append",
        &[
            model_assistant_typescript("program-message-subagents", message_source),
            model_tool("program-message-subagents", "run_typescript"),
        ],
    );
    let alpha_notification = subagent_notification(
        "notification-alpha-completed",
        "alpha",
        "alpha completed with log evidence",
    );
    runtime.receive_notification(turn_id, &alpha_notification, &[&waiting_completion]);
    let beta_notification = subagent_notification(
        "notification-beta-completed",
        "beta",
        "beta completed with test evidence",
    );
    runtime.receive_notification(turn_id, &beta_notification, &[&waiting_completion]);
    let notification_completion = runtime
        .apply(
            text_completion(&waiting_completion, "waiting for both researchers"),
            running(turn_id)
                .dispatch(llm_call(3))
                .observe(assistant_text_committed(
                    turn_id,
                    &waiting_completion,
                    "waiting for both researchers",
                ))
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&alpha_notification),
                ))
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&beta_notification),
                )),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &notification_completion,
        "append",
        &[
            model_assistant_text("waiting for both researchers"),
            model_notifications(&[&alpha_notification, &beta_notification]),
        ],
    );
    runtime.finish_turn_with_text(
        turn_id,
        &notification_completion,
        "both reports incorporated",
    );

    // Assert
    assert_eq!(alpha_spawn["call"]["arguments"]["agentName"], "alpha");
    assert_eq!(beta_spawn["call"]["arguments"]["agentName"], "beta");
    assert_eq!(alpha_message["call"]["arguments"]["message"], "Check logs");
    assert_eq!(beta_message["call"]["arguments"]["message"], "Check tests");
}

///
/// *Prepare*: A model starts one enabled background process through programmatic tool calling.
/// *Do*: Accept the start result, deliver the process completion notification while the model is pending, and finish after Core exposes it to the model.
/// *Assert*: The Runtime route, retained completion, notification observation, and final turn state are all explicit.
///
#[test]
fn one_background_process_completion_notification_reaches_the_model() {
    // Prepare
    let turn_id = "turn-one-process";
    let mut runtime = SynchronousRuntime::new(config_with_background_processes());
    let first_completion = runtime.start_turn(turn_id, "start one background process");
    let source = "async function main() { return tools.process.start({ command: 'build' }); }";

    // Do
    let process_action = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            "program-one-process",
            source,
            [runtime_tool("process.start", json!({"command": "build"}))],
        )
        .only_action();
    let process_result = structured_tool_success(
        &process_action,
        json!({"processId": "process-1", "status": "running"}),
    );
    let outer_result = run_typescript_success(json!({
        "processId": "process-1",
        "status": "running",
    }));
    let waiting_completion = runtime
        .apply(
            process_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&process_action, &process_result)
                .observe_tool_execution_finished("program-one-process", outer_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &waiting_completion,
        "append",
        &[
            model_assistant_typescript("program-one-process", source),
            model_tool("program-one-process", "run_typescript"),
        ],
    );
    let notification = process_notification(
        "notification-process-completed",
        "process-1",
        "background build completed",
    );
    runtime.receive_notification(turn_id, &notification, &[&waiting_completion]);
    let notification_completion = runtime
        .apply(
            text_completion(&waiting_completion, "waiting for the build"),
            running(turn_id)
                .dispatch(llm_call(2))
                .observe(assistant_text_committed(
                    turn_id,
                    &waiting_completion,
                    "waiting for the build",
                ))
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&notification),
                )),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &notification_completion,
        "append",
        &[
            model_assistant_text("waiting for the build"),
            model_notifications(&[&notification]),
        ],
    );
    runtime.finish_turn_with_text(
        turn_id,
        &notification_completion,
        "background build incorporated",
    );

    // Assert
    assert_eq!(process_action["call"]["name"], "process.start");
    assert_eq!(process_action["call"]["arguments"]["command"], "build");
}

///
/// *Prepare*: A post-LLM hook is pending with an uncommitted assistant message.
/// *Do*: Receive a notification, restore the checkpoint, and accept the retained assistant message.
/// *Assert*: Core keeps the hook and delivers the notification only after it commits the assistant message.
///
#[test]
fn notification_during_post_llm_hook_waits_for_the_accepted_assistant_message() {
    // Prepare
    let turn_id = "turn-notification-post-llm-hook";
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": "notification-post-llm",
        "point": "post_llm_call",
        "order": 0,
        "selector": {"type": "always"},
    }]))
    .expect("post-LLM hook config deserializes");
    let mut runtime = SynchronousRuntime::new(config);
    let original_completion = runtime.start_turn(turn_id, "wait for the background result");
    let retained_hook = runtime
        .apply(
            text_completion(&original_completion, "answer before notification"),
            running(turn_id).dispatch(hook_call(
                "post_llm_call",
                &["notification-post-llm"],
                json!({"candidate": text_candidate("answer before notification")}),
            )),
        )
        .only_action();
    let notification = process_notification(
        "notification-during-post-llm",
        "process-hook",
        "background work completed during the hook",
    );

    // Do
    runtime.receive_notification(turn_id, &notification, &[&retained_hook]);
    runtime.restart_from_checkpoint();
    let follow_up_completion = runtime
        .apply(
            hook_completed(
                &retained_hook,
                "post_llm_call",
                json!({"type": "accept", "acceptance": {"type": "candidate"}}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(
                    turn_id,
                    &original_completion,
                    "answer before notification",
                ))
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&notification),
                )),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &follow_up_completion,
        "replace",
        &[
            model_system(),
            model_user_text("wait for the background result"),
            model_assistant_text("answer before notification"),
            model_notifications(&[&notification]),
        ],
    );
    let final_hook = runtime
        .apply(
            text_completion(&follow_up_completion, "background result received"),
            running(turn_id).dispatch(hook_call(
                "post_llm_call",
                &["notification-post-llm"],
                json!({"candidate": text_candidate("background result received")}),
            )),
        )
        .only_action();
    runtime.apply(
        hook_completed(
            &final_hook,
            "post_llm_call",
            json!({"type": "accept", "acceptance": {"type": "candidate"}}),
        ),
        completed(
            turn_id,
            vec![json!({"type": "text", "text": "background result received"})],
        )
        .observe(assistant_text_committed(
            turn_id,
            &follow_up_completion,
            "background result received",
        ))
        .observe(turn_completed(turn_id, "background result received")),
    );

    // Assert
    assert_eq!(retained_hook["hook"], "post_llm_call");
    assert_eq!(final_hook["hook"], "post_llm_call");
}

///
/// *Prepare*: Two tool Actions from one assistant message are running in parallel.
/// *Do*: Receive a notification, restore the checkpoint, and return both tool results.
/// *Assert*: Core waits for the complete tool batch, then delivers the notification after both tool results.
///
#[test]
fn notification_during_parallel_tools_waits_for_the_complete_batch() {
    // Prepare
    let turn_id = "turn-notification-tools";
    let mut runtime = SynchronousRuntime::new(config());
    let original_completion = runtime.start_turn(turn_id, "read both reports");
    let calls = vec![
        tool_call("notification-tool-a", "read_file", json!({"path": "a.txt"})),
        tool_call("notification-tool-b", "read_file", json!({"path": "b.txt"})),
    ];
    let tools = runtime
        .complete_with_tool_calls(
            turn_id,
            &original_completion,
            &calls,
            [
                runtime_tool("file_system.read_file", json!({"path": "a.txt"})),
                runtime_tool("file_system.read_file", json!({"path": "b.txt"})),
            ],
        )
        .actions();
    let [first_tool, second_tool]: [Value; 2] = tools.try_into().expect("two tools are running");
    let notification = process_notification(
        "notification-during-tools",
        "process-tools",
        "background work completed during the tool batch",
    );

    // Do
    runtime.receive_notification(turn_id, &notification, &[&first_tool, &second_tool]);
    runtime.restart_from_checkpoint();
    let first_result = text_tool_success(&first_tool, "a contents");
    runtime.apply(
        first_result.clone(),
        running(turn_id)
            .keep(&second_tool)
            .observe_completed_tool_result(&first_tool, &first_result),
    );
    let second_result = text_tool_success(&second_tool, "b contents");
    let completion = runtime
        .apply(
            second_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&second_tool, &second_result)
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&notification),
                )),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &completion,
        "replace",
        &[
            model_system(),
            model_user_text("read both reports"),
            model_assistant_tool_calls(&calls),
            model_tool_text("notification-tool-a", "read_file", "a contents"),
            model_tool_text("notification-tool-b", "read_file", "b contents"),
            model_notifications(&[&notification]),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &completion, "both reports and the update received");

    // Assert
    assert_eq!(first_tool["call_id"], "notification-tool-a");
    assert_eq!(second_tool["call_id"], "notification-tool-b");
}

///
/// *Prepare*: One program starts a subagent and a background process in parallel, and both later complete while a model call is pending.
/// *Do*: Deliver both notifications, steer the active turn, and finish the retained model call.
/// *Assert*: Steering keeps the completion, then enters model context after its tool-free assistant message together with both notifications.
///
#[test]
fn steering_combines_subagent_and_background_process_notifications() {
    // Prepare
    let turn_id = "turn-combined-steering";
    let mut runtime = SynchronousRuntime::new(config_with_background_processes());
    let first_completion = runtime.start_turn(turn_id, "delegate research and start the build");
    let source = "async function main() { return Promise.all([tools.agent.spawn({ agentName: 'researcher', message: 'Investigate' }), tools.process.start({ command: 'build' })]); }";
    let tool_actions = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            "program-combined-work",
            source,
            [
                runtime_tool(
                    "subagent.spawn",
                    json!({"agentName": "researcher", "message": "Investigate"}),
                ),
                runtime_tool("process.start", json!({"command": "build"})),
            ],
        )
        .actions();
    let [subagent_action, process_action]: [Value; 2] = tool_actions
        .try_into()
        .expect("combined work dispatches two actions");
    assert_eq!(subagent_action["call"]["name"], "subagent.spawn");
    assert_eq!(process_action["call"]["name"], "process.start");
    let subagent_result = structured_tool_success(&subagent_action, json!({"type": "success"}));
    runtime.apply(
        subagent_result.clone(),
        running(turn_id)
            .keep(&process_action)
            .observe_tool_result(&subagent_action, &subagent_result),
    );
    let process_result = structured_tool_success(
        &process_action,
        json!({"processId": "process-1", "status": "running"}),
    );
    let waiting_completion = runtime
        .apply(
            process_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&process_action, &process_result)
                .observe_tool_execution_finished(
                    "program-combined-work",
                    run_typescript_success(json!([
                        {"type": "success"},
                        {"processId": "process-1", "status": "running"},
                    ])),
                ),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &waiting_completion,
        "append",
        &[
            model_assistant_typescript("program-combined-work", source),
            model_tool("program-combined-work", "run_typescript"),
        ],
    );

    // Do
    let subagent_notification = subagent_notification(
        "notification-combined-subagent",
        "researcher",
        "researcher completed combined work",
    );
    runtime.receive_notification(turn_id, &subagent_notification, &[&waiting_completion]);
    let process_notification = process_notification(
        "notification-combined-process",
        "process-1",
        "background build completed combined work",
    );
    runtime.receive_notification(turn_id, &process_notification, &[&waiting_completion]);
    runtime.receive_steering(
        turn_id,
        "focus the final answer on regressions",
        &[&waiting_completion],
    );
    let follow_up_completion = runtime
        .apply(
            text_completion(&waiting_completion, "combined work before steering"),
            running(turn_id)
                .dispatch(llm_call(2))
                .observe(assistant_text_committed(
                    turn_id,
                    &waiting_completion,
                    "combined work before steering",
                ))
                .observe(turn_steered(
                    turn_id,
                    "focus the final answer on regressions",
                ))
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&subagent_notification),
                ))
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&process_notification),
                )),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &follow_up_completion,
        "append",
        &[
            model_assistant_text("combined work before steering"),
            model_user_text("focus the final answer on regressions"),
            model_notifications(&[&subagent_notification, &process_notification]),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &follow_up_completion, "combined work redirected");

    // Assert
    assert_eq!(follow_up_completion["type"], "llm_call");
}
