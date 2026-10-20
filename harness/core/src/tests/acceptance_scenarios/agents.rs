use super::*;

/// *Prepare*: A valid Session has its normal discovery description.
/// *Do*: Reconfigure with an empty or whitespace-only discovery description.
/// *Assert*: Each command is rejected atomically, preserving the model-facing catalog.
#[test]
fn operator_discovery_description_reconfiguration_rejects_blank_text() {
    // Prepare
    let initial = config();
    let mut runtime = SynchronousRuntime::new(initial.clone());
    for description in ["", " \n\t"] {
        let mut settings = serde_json::to_value(&initial.settings).unwrap();
        settings["prompts"]["discovery_description"] = json!(description);

        // Do / Assert
        runtime.reject(
            json!({
                "type":"reconfigure", "changes":[{"type":"settings", "value":settings}],
            }),
            json!({"code":"invalid_command", "error":{
                "code":"invalid_command", "message":"prompt must not be empty",
                "retryable":false, "details":null,
            }}),
        );
    }
    let action = runtime.start_turn("unchanged", "inspect");
    let discovery = action["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "search_tool_functions")
        .unwrap();
    assert_eq!(
        discovery["description"],
        initial.settings.prompts.discovery_description
    );
}

/// *Prepare*: Unattributed-task wording contains escaped literal braces.
/// *Do*: Admit a task without tree context through the serialized Step Protocol.
/// *Assert*: The model receives the once-rendered literal beside unchanged task content.
#[test]
fn operator_unattributed_task_template_renders_escaped_braces() {
    // Prepare
    let mut settings = serde_json::to_value(config()).unwrap();
    settings["settings"]["prompts"]["agent_messages"]["task"] = json!("Use {{context}}.\n");
    let mut runtime = SynchronousRuntime::new(serde_json::from_value(settings).unwrap());
    let content = json!([
        {"type":"text", "text":"Agent task from /root:\n"},
        {"type":"text", "text":"Use {context}.\n"},
        {"type":"text", "text":"Read {{this}} unchanged."},
    ]);

    // Do
    let action = runtime
        .apply(
            json!({
                "type":"agent_message", "turn_id":"task", "mode":"queue",
                "message": {"kind":"task", "sender_agent_path":"/root",
                    "content":[{"type":"text", "text":"Read {{this}} unchanged."}]},
            }),
            running("task").dispatch(llm_call(0)).observe(json!({
                "type":"turn_started", "turn_id":"task", "content":content,
            })),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &action,
        "replace",
        &[model_system(), json!({"role":"user", "content":content})],
    );
}

/// *Prepare*: A Session has already published its model tool catalog.
/// *Do*: Change each operator-owned tool prompt family between Turns.
/// *Assert*: The next completion replaces the tool cache and uses the new wording.
#[test]
fn operator_prompt_reconfiguration_invalidates_the_tool_catalog() {
    // Prepare
    for family in [
        "programs",
        "agent_tools",
        "wait_description",
        "discovery_description",
    ] {
        let mut initial = serde_json::to_value(config()).unwrap();
        initial["settings"]["tools"]["model_wait"] = json!({"mode":"enabled"});
        let mut runtime = SynchronousRuntime::new(serde_json::from_value(initial.clone()).unwrap());
        let first = runtime.start_turn("before", "inspect");
        runtime.finish_turn_with_text("before", &first, "done");
        let mut settings = initial["settings"].clone();
        match family {
            "programs" => settings["prompts"][family]["description"] = json!("UPDATED PROMPT"),
            "agent_tools" => settings["prompts"][family]["guidance"] = json!("UPDATED PROMPT"),
            _ => settings["prompts"][family] = json!("UPDATED PROMPT"),
        }

        // Do
        runtime.reconfigure_settings(
            serde_json::from_value(settings).unwrap(),
            completed("before", vec![json!({"type":"text", "text":"done"})]),
        );
        let next = runtime
            .apply(
                user_message("after", "inspect again", "queue"),
                running("after")
                    .dispatch(llm_call(0))
                    .observe(turn_started("after", "inspect again")),
            )
            .only_action();

        // Assert
        assert_eq!(
            next["model_input"]["tool_catalog"]["type"], "replace",
            "{family}"
        );
        assert!(next.to_string().contains("UPDATED PROMPT"), "{family}");
    }
}

/// *Prepare*: Operator guidance replaces the generic introduction, autonomy, and discovery text.
/// *Do*: Start one ordinary Turn through the serialized Step Protocol.
/// *Assert*: The model receives the configured guidance beside unchanged authored instructions.
#[test]
fn operator_guidance_replaces_generic_instructions_and_discovery_description() {
    // Prepare
    let mut settings = serde_json::to_value(config()).unwrap();
    settings["system_instructions"] = json!("AUTHORED INSTRUCTIONS");
    settings["settings"]["prompts"]["guidance"] =
        json!({"intro":"OPERATOR INTRO", "autonomy":"OPERATOR AUTONOMY"});
    settings["settings"]["prompts"]["discovery_description"] = json!("OPERATOR DISCOVERY");
    let mut runtime = SynchronousRuntime::new(serde_json::from_value(settings).unwrap());

    // Do
    let action = runtime.start_turn("guidance", "work");

    // Assert
    let request = action.to_string();
    for expected in [
        "OPERATOR INTRO",
        "OPERATOR AUTONOMY",
        "OPERATOR DISCOVERY",
        "AUTHORED INSTRUCTIONS",
    ] {
        assert!(request.contains(expected));
    }
    assert!(!request.contains("Act directly and proactively"));
}

/// *Prepare*: Operator wording differs from native defaults for agents, programs, and waiting.
/// *Do*: Start a Turn and discover the configured spawn tool.
/// *Assert*: System guidance and executable tool descriptions use the resolved strings.
#[test]
fn operator_prompts_reach_model_guidance_and_tool_catalogs() {
    // Prepare
    let mut settings = serde_json::to_value(config()).unwrap();
    settings["settings"]["prompts"]["agent_tools"] = json!({
        "guidance":"CUSTOM AGENT GUIDANCE", "spawn":"CUSTOM SPAWN DESCRIPTION",
    });
    settings["settings"]["prompts"]["programs"] = json!({
        "description":"CUSTOM PROGRAM DESCRIPTION",
        "tool_groups":"CUSTOM DISCOVERY GUIDANCE\n{groups}",
        "current_time":"CUSTOM TIME GUIDANCE",
    });
    settings["settings"]["tools"]["model_wait"] = json!({"mode":"enabled"});
    settings["settings"]["prompts"]["wait_description"] = json!("CUSTOM WAIT DESCRIPTION");
    let mut runtime = SynchronousRuntime::new(serde_json::from_value(settings).unwrap());

    // Do
    let first = runtime.start_turn("operator", "inspect capabilities");

    // Assert
    let request = first.to_string();
    for expected in [
        "CUSTOM AGENT GUIDANCE",
        "CUSTOM DISCOVERY GUIDANCE",
        "CUSTOM TIME GUIDANCE",
        "CUSTOM PROGRAM DESCRIPTION",
        "CUSTOM WAIT DESCRIPTION",
    ] {
        assert!(request.contains(expected), "missing {expected}");
    }
    let next = runtime
        .complete_with_typescript_program(
            "operator",
            &first,
            "spawn",
            "return await tools.agents.spawn({name: 'worker', message: 'inspect'});",
            [runtime_tool(
                "agents.spawn",
                json!({"name":"worker", "message":"inspect"}),
            )],
        )
        .only_action();
    assert_eq!(next["call"]["name"], "agents.spawn");
}

/// *Prepare*: A child imports a nested parent's conversation and operator task wording.
/// *Do*: Admit its first task through the serialized Step Protocol.
/// *Assert*: The assignment follows history and names the immediate source and child.
#[test]
fn operator_task_framing_identifies_inherited_source_and_recipient() {
    // Prepare
    let mut parent = SynchronousRuntime::new(config());
    let first = parent.start_turn("parent", "Inherited fact: JADE-42.");
    parent.finish_turn_with_text("parent", &first, "Remembered.");
    let mut settings = serde_json::to_value(config()).unwrap();
    settings["system_instructions"] = json!("CHILD AUTHORED INSTRUCTIONS");
    settings["settings"]["prompts"]["agent_messages"] = json!({
        "inherited_task": "Copied from {source_agent_path}. You are {recipient_agent_path}. Task from {sender_agent_path}:\n",
    });
    let mut child = SynchronousRuntime::new_with_conversation(
        serde_json::from_value(settings).unwrap(),
        parent.export_conversation("all"),
    );
    let content = json!([
        {"type":"text", "text":"Agent task from /root/research:\n"},
        {"type":"text", "text":"Copied from /root/research. You are /root/research/check. Task from /root/research:\n"},
        {"type":"text", "text":"Report the inherited fact."},
    ]);

    // Do
    let action = child.apply(json!({
        "type":"agent_message", "turn_id":"child", "mode":"queue",
        "message":{
            "kind":"task", "sender_agent_path":"/root/research",
            "context":{"type":"inherited", "source_agent_path":"/root/research", "recipient_agent_path":"/root/research/check"},
            "content":[{"type":"text", "text":"Report the inherited fact."}],
        },
    }), running("child").dispatch(llm_call(0)).observe(json!({
        "type":"turn_started", "turn_id":"child", "content":content,
    }))).only_action();

    // Assert
    child.assert_last_model_message_update(
        &action,
        "replace",
        &[
            model_system(),
            model_user_text("Inherited fact: JADE-42."),
            model_assistant_text("Remembered."),
            json!({"role":"user", "content":content}),
        ],
    );
    let system = action["model_input"]["messages"]["messages"][0].to_string();
    assert!(system.contains("CHILD AUTHORED INSTRUCTIONS"));
    assert!(!system.contains("Copied from"));
}

fn attributed_agent_content(kind: &str, sender: &str, text: &str) -> Value {
    let mut content = vec![json!({
        "type":"text", "text":format!("Agent {kind} from {sender}:\n"),
    })];
    if kind == "task" {
        content.push(json!({
            "type":"text",
            "text":"You are a separate agent with your own assigned task. Use prior conversation only as context; do not resume another agent's work. Follow the task below.\n",
        }));
    }
    content.push(json!({"type":"text", "text":text}));
    json!(content)
}

/// *Prepare*: A child inherits a parent's completed Turn and current unfinished request.
/// *Do*: Assign a task, complete it, restore the same child, and assign another task.
/// *Assert*: Both tasks are framed separately from unchanged history, tools, and provenance.
#[test]
fn delegated_tasks_frame_inherited_context_without_resuming_parent_work() {
    // Prepare
    let mut parent_config = config();
    parent_config.system_instructions = "PARENT AUDIT INSTRUCTIONS".into();
    let mut parent = SynchronousRuntime::new(parent_config);
    let first = parent.start_turn("remember", "Remember audit fact CORAL-31.");
    let tools = first["model_input"]["tool_catalog"]["tools"].clone();
    parent.finish_turn_with_text("remember", &first, "Recorded.");
    parent.apply(
        user_message(
            "parent-work",
            "Resume the audit and spawn helpers.",
            "queue",
        ),
        running("parent-work")
            .dispatch(llm_call(0))
            .observe(turn_started(
                "parent-work",
                "Resume the audit and spawn helpers.",
            )),
    );
    let seed = parent.export_conversation("all");
    let mut child_config = config();
    child_config.task_id = "child".into();
    child_config.system_instructions = "CHILD AUDIT INSTRUCTIONS".into();
    let mut child = SynchronousRuntime::new_with_conversation(child_config, seed.clone());
    let mut expected_messages = vec![
        model_system(),
        model_user_text("Remember audit fact CORAL-31."),
        model_assistant_text("Recorded."),
        model_user_text("Resume the audit and spawn helpers."),
    ];

    // Do / Assert
    for (turn_id, task, answer) in [
        ("first-task", "Report the audit fact only.", "CORAL-31"),
        ("next-task", "Count the characters in that fact.", "8"),
    ] {
        let content = attributed_agent_content("task", "/root", task);
        let action = child
            .apply(
                json!({
                    "type":"agent_message", "turn_id":turn_id, "mode":"queue",
                    "message":{
                        "sender_agent_path":"/root", "kind":"task",
                        "content":[{"type":"text", "text":task}],
                    },
                }),
                running(turn_id).dispatch(llm_call(0)).observe(json!({
                    "type":"turn_started", "turn_id":turn_id, "content":content,
                })),
            )
            .only_action();
        expected_messages.push(json!({"role":"user", "content":content}));
        child.assert_last_model_message_update(&action, "replace", &expected_messages);
        let system = action["model_input"]["messages"]["messages"][0].to_string();
        assert!(system.contains("CHILD AUDIT INSTRUCTIONS"));
        assert!(!system.contains("PARENT AUDIT INSTRUCTIONS"));
        assert_eq!(action["model_input"]["tool_catalog"]["tools"], tools);
        child.finish_turn_with_text(turn_id, &action, answer);
        expected_messages.push(model_assistant_text(answer));
        child.restart_from_checkpoint();
    }
    assert_eq!(parent.export_conversation("all"), seed);
}

/// *Prepare*: A parent's program call that spawns a forked helper is still running.
/// *Do*: Export the parent's conversation, give the child its task, and let the parent continue.
/// *Assert*: The child sees that call closed by the default note before its own task, and both
/// next requests repeat the parent's messages up to and including the call.
#[test]
fn forked_child_sees_the_running_spawn_call_closed_before_its_task() {
    // Prepare
    let defaults: Value =
        serde_json::from_str(include_str!("../../core/prompt/default_prompts.json")).unwrap();
    let note = defaults["agent_messages"]["inherited_pending_call"]
        .as_str()
        .unwrap();
    let request = "Spawn a helper to check the notes, then wait for it.";
    let source = "async function main() { return tools.agents.spawn({ name: 'helper', message: 'Check the notes' }); }";
    let mut parent = SynchronousRuntime::new(config());
    let first = parent.start_turn("parent", request);
    let spawn = parent
        .complete_with_typescript_program(
            "parent",
            &first,
            "spawn-helper",
            source,
            [runtime_tool(
                "agents.spawn",
                json!({"name":"helper", "message":"Check the notes"}),
            )],
        )
        .only_action();
    let content = json!([
        {"type":"text", "text":"Agent task from /root:\n"},
        {"type":"text", "text":"The inherited conversation above was copied from /root. You are /root/helper. Your current task from /root is:\n"},
        {"type":"text", "text":"Check the notes"},
    ]);

    // Do
    let mut child_config = config();
    child_config.task_id = "child".into();
    let mut child =
        SynchronousRuntime::new_with_conversation(child_config, parent.export_conversation("all"));
    let child_action = child
        .apply(
            json!({
                "type":"agent_message", "turn_id":"child", "mode":"queue",
                "message":{
                    "kind":"task", "sender_agent_path":"/root",
                    "context":{"type":"inherited", "source_agent_path":"/root", "recipient_agent_path":"/root/helper"},
                    "content":[{"type":"text", "text":"Check the notes"}],
                },
            }),
            running("child").dispatch(llm_call(0)).observe(json!({
                "type":"turn_started", "turn_id":"child", "content":content,
            })),
        )
        .only_action();
    let spawn_result = structured_tool_success(&spawn, json!({"type": "success"}));
    let parent_next = parent
        .apply(
            spawn_result.clone(),
            running("parent")
                .dispatch(llm_call(1))
                .observe_tool_result(&spawn, &spawn_result)
                .observe_tool_execution_finished(
                    "spawn-helper",
                    run_typescript_success(json!({"type": "success"})),
                ),
        )
        .only_action();

    // Assert
    child.assert_last_model_message_update(
        &child_action,
        "replace",
        &[
            model_system(),
            model_user_text(request),
            model_assistant_typescript("spawn-helper", source),
            model_tool_text("spawn-helper", "run_typescript", note),
            json!({"role":"user", "content":content}),
        ],
    );
    parent.assert_last_model_message_update(
        &parent_next,
        "append",
        &[
            model_assistant_typescript("spawn-helper", source),
            model_tool("spawn-helper", "run_typescript"),
        ],
    );
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
            json!({"outcome":"completed", "output":[{"type":"text", "text":"Two of five steps done."}], "stop_reason":"iteration_limit"}),
            "Status: stopped, iteration limit reached before the agent finished\nPartial output: [{\"type\":\"text\",\"text\":\"Two of five steps done.\"}]\nTo let it continue with its history, send a message to /root/child.",
        ),
        (
            json!({"outcome":"completed", "output":[], "stop_reason":"iteration_limit"}),
            "Status: stopped, iteration limit reached before the agent finished\nTo let it continue with its history, send a message to /root/child.",
        ),
        (
            json!({"outcome":"completed", "output":[{"type":"text", "text":"Skipped by policy"}], "stop_reason":"skipped"}),
            "Status: completed\nOutput: [{\"type\":\"text\",\"text\":\"Skipped by policy\"}]",
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
            "Status: cancelled\nReason: User request\nTo let it continue with its history, send a message to /root/child.",
        ),
        (
            json!({"outcome":"cancelled", "reason":"User request", "output":[{"type":"text", "text":"Halfway there."}]}),
            "Status: cancelled\nReason: User request\nPartial output: [{\"type\":\"text\",\"text\":\"Halfway there.\"}]\nTo let it continue with its history, send a message to /root/child.",
        ),
        (
            json!({"outcome":"cancelled"}),
            "Status: cancelled\nTo let it continue with its history, send a message to /root/child.",
        ),
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
        "Status: cancelled\nReason: Superseded\nTo let it continue with its history, send a message to /root/first.",
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

/// *Prepare*: The parent cannot resume an idle child with a message: its tree policy keeps such
/// messages pending, or it has no `send_message`.
/// *Do*: Deliver a cancelled and an iteration-limited child result.
/// *Assert*: The result keeps its status and partial output but has no continue hint.
#[test]
fn unfinished_agent_results_omit_the_continue_hint_when_a_message_cannot_resume_the_child() {
    for subagents in [
        json!({"mode":"enabled", "idle_message_starts":"none"}),
        json!({"mode":"enabled", "operations":["list", "spawn", "interrupt"]}),
    ] {
        for (termination, rendered) in [
            (
                json!({"outcome":"cancelled", "reason":"User request", "output":[{"type":"text", "text":"Halfway there."}]}),
                "Status: cancelled\nReason: User request\nPartial output: [{\"type\":\"text\",\"text\":\"Halfway there.\"}]",
            ),
            (
                json!({"outcome":"completed", "output":[], "stop_reason":"iteration_limit"}),
                "Status: stopped, iteration limit reached before the agent finished",
            ),
        ] {
            // Prepare
            let mut settings = config();
            settings.settings.tools.subagents = serde_json::from_value(subagents.clone()).unwrap();
            let mut runtime = SynchronousRuntime::new(settings);
            let content = attributed_agent_content("result", "/root/child", rendered);

            // Do
            let first = runtime.apply(json!({
                "type":"agent_message", "turn_id":"parent-turn", "mode":"queue",
                "message":{"sender_agent_path":"/root/child", "kind":"result", "termination":termination},
            }), running("parent-turn").dispatch(llm_call(0)).observe(json!({
                "type":"turn_started", "turn_id":"parent-turn", "content":content,
            }))).only_action();

            // Assert
            runtime.assert_last_model_message_update(
                &first,
                "replace",
                &[model_system(), json!({"role":"user", "content":content})],
            );
            assert!(
                !first["model_input"]
                    .to_string()
                    .contains("send a message to")
            );
            runtime.finish_turn_with_text("parent-turn", &first, "done");
        }
    }
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

/// Frozen checkpoints predate Turn groups; restoring puts each message in Turn 0.
fn restored_frozen_checkpoint(fixture: &str) -> Value {
    let mut checkpoint: Value = serde_json::from_str(fixture).unwrap();
    for message in checkpoint["context"]["messages"].as_array_mut().unwrap() {
        assert!(message.get("turn_index").is_none());
        message["turn_index"] = json!(0);
    }
    checkpoint
}

fn frozen_agent_action(key: &str) -> Value {
    let actions: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/checkpoints/retired-v1-agents/deprecated-agent-actions.json"
    )))
    .unwrap();
    actions[key].clone()
}

/// *Prepare*: An earlier V1 program called a retired agent name and has received an image-bearing spawn result that awaits its post-hook.
/// *Do*: Restore the Hook Action, preserve its result, and resume the program.
/// *Assert*: The old Action/result survive unchanged; the retired name no longer exists, so the program fails with a plain TypeError and the model is called.
#[test]
fn frozen_retired_agent_post_hook_commits_then_program_fails() {
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
    // The checkpoint predates recorded pauses: its post-hook wait pauses the
    // program's time budget from the program's start.
    let mut restored = restored_frozen_checkpoint(fixture);
    let execution = &mut restored["turn"]["phase"]["batch"]["executions"][0]["execution"];
    execution["paused_since_unix_ms"] = execution["started_at_unix_ms"].clone();
    assert_eq!(runtime.checkpoint_value(), restored);
    runtime.restart_from_checkpoint();
    let result = hook["input"]["tool_result"].clone();
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
                .observe_tool_execution_finished(
                    "program",
                    run_typescript_uncaught_error(
                        "TypeError",
                        "Cannot read properties of undefined (reading 'spawn')",
                    ),
                ),
        )
        .only_action();
    runtime.finish_turn_with_text("old-agent", &completion, "done");
}

/// *Prepare*: A frozen V1 program has an issued spawn using a retired agent name.
/// *Do*: Restore with current configuration and deliver its result.
/// *Assert*: The already-issued Action keeps its identity; the program then fails with a plain TypeError instead of reaching its timer.
#[test]
fn frozen_retired_agent_spawn_keeps_action_identity_then_program_fails() {
    let spawn = frozen_agent_action("pending_action");
    let fixture = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/checkpoints/retired-v1-agents/active-deprecated-agent-spawn.json"
    ));
    let mut runtime = SynchronousRuntime::from_checkpoint(config(), fixture, vec![spawn.clone()]);
    assert_eq!(
        runtime.checkpoint_value(),
        restored_frozen_checkpoint(fixture)
    );
    runtime.restart_from_checkpoint();
    let response = structured_tool_success(&spawn, json!({"type":"success"}));
    let completion = runtime
        .apply(
            response.clone(),
            running("old-agent")
                .dispatch(llm_call(1))
                .observe_tool_result(&spawn, &response)
                .observe_tool_execution_finished(
                    "program",
                    run_typescript_uncaught_error(
                        "TypeError",
                        "Cannot read properties of undefined (reading 'spawn')",
                    ),
                ),
        )
        .only_action();
    runtime.finish_turn_with_text("old-agent", &completion, "done");
}

/// *Prepare*: A frozen V1 program recorded a spawn result under a retired agent name and is awaiting a timer.
/// *Do*: Complete the timer.
/// *Assert*: The program fails with a plain TypeError at the retired name, dispatches nothing new, and the model is called.
#[test]
fn frozen_completed_retired_agent_spawn_fails_without_new_effects() {
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
    let completion = runtime
        .apply(
            response.clone(),
            running("old-agent")
                .dispatch(llm_call(1))
                .observe_tool_result(&timer, &response)
                .observe_tool_execution_finished(
                    "program",
                    run_typescript_uncaught_error(
                        "TypeError",
                        "Cannot read properties of undefined (reading 'spawn')",
                    ),
                ),
        )
        .only_action();
    runtime.finish_turn_with_text("old-agent", &completion, "done");
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

/// *Prepare*: A program catches a call to a retired agent name, then calls `tools.agents.list`.
/// *Do*: Run the program and answer its one `agents.list` Action.
/// *Assert*: The retired name is absent from `tools` and throws a plain TypeError; `agents.list` is dispatched once and the program returns.
#[test]
fn retired_agent_names_are_unknown_and_the_next_call_runs_once() {
    for expression in [
        "tools.agent.list({})",
        "tools.subagent.stop({name:'child'})",
        "tools.collaboration.spawn_agent({name:'child',message:'work'})",
    ] {
        // Prepare
        let turn_id = "retired";
        let mut runtime = SynchronousRuntime::new(config());
        let first = runtime.start_turn(turn_id, "coordinate");
        let source = format!(
            "let error = null; \
             try {{ await {expression}; }} catch (caught) {{ error = caught.name; }} \
             const listed = await tools.agents.list({{}}); \
             const retired = Object.keys(tools).filter((name) => ['agent', 'subagent', 'collaboration'].includes(name)); \
             return {{ error, listed, retired }};"
        );

        // Do
        let list = runtime
            .complete_with_typescript_program(
                turn_id,
                &first,
                "program",
                &source,
                [runtime_tool("agents.list", json!({}))],
            )
            .only_action();
        let response = structured_tool_success(&list, json!({"agents": []}));
        let next = runtime
            .apply(
                response.clone(),
                running(turn_id)
                    .dispatch(llm_call(1))
                    .observe_tool_result(&list, &response)
                    .observe_tool_execution_finished(
                        "program",
                        run_typescript_success(json!({
                            "error": "TypeError",
                            "listed": {"agents": []},
                            "retired": [],
                        })),
                    ),
            )
            .only_action();

        // Assert
        runtime.finish_turn_with_text(turn_id, &next, "done");
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
