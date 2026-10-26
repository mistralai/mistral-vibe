use super::*;
use crate::core::Message;

/// *Prepare*: Hosts explicitly declare whether they can import parent conversation.
/// *Do*: Call spawn through real program execution with valid and invalid contexts.
/// *Assert*: Unsupported and malformed contexts dispatch no child creation effect.
#[test]
fn conversation_spawn_schema_matches_host_support_and_rejects_invalid_contexts() {
    let valid_contexts = [
        json!("fresh"),
        json!({"mode": "forked"}),
        json!({"mode": "forked", "turns": "all"}),
        json!({"mode": "forked", "turns": 1}),
        json!({"mode": "forked", "turns": 4_294_967_295u32}),
    ];
    let invalid_contexts = [
        json!("forked"),
        json!("all"),
        json!("none"),
        json!(""),
        json!(1),
        json!(true),
        json!(null),
        json!({"mode": "fresh"}),
        json!({"turns": 1}),
        json!({"mode": "forked", "turns": 0}),
        json!({"mode": "forked", "turns": -1}),
        json!({"mode": "forked", "turns": 1.5}),
        json!({"mode": "forked", "turns": "1"}),
        json!({"mode": "forked", "turns": 4_294_967_296u64}),
        json!({"mode": "forked", "turns": 1, "summary": true}),
    ];
    let contexts = std::iter::once((None, true))
        .chain(
            valid_contexts
                .into_iter()
                .map(|context| (Some(context), true)),
        )
        .chain(
            invalid_contexts
                .into_iter()
                .map(|context| (Some(context), false)),
        )
        .collect::<Vec<_>>();
    for supported in [false, true] {
        for (context, valid_context) in &contexts {
            // Prepare
            let mut settings = config();
            settings.settings.tools.subagents = serde_json::from_value(json!({
                "mode":"enabled", "conversation_forking":supported,
            }))
            .unwrap();
            let mut runtime = SynchronousRuntime::new(settings);
            let first = runtime.start_turn("parent", "delegate");
            let mut args = json!({"name":"child", "message":"work"});
            if let Some(value) = context {
                args["context"] = value.clone();
            }
            let valid = context.is_none() || supported && *valid_context;
            let source = format!(
                "try {{ return await tools.agents.spawn({args}); }} catch (error) {{ return 'rejected'; }}"
            );

            // Do / Assert
            if valid {
                let spawn = runtime
                    .complete_with_typescript_program(
                        "parent",
                        &first,
                        "spawn",
                        &source,
                        [runtime_tool("agents.spawn", args.clone())],
                    )
                    .only_action();
                assert_eq!(spawn["call"]["arguments"], args);
            } else {
                runtime.apply(
                    run_typescript_completion(action_id(&first), "spawn", &source),
                    running("parent")
                        .dispatch(llm_call(1))
                        .observe(tool_execution_started("parent", "spawn"))
                        .observe_tool_execution_finished(
                            "spawn",
                            run_typescript_success(json!("rejected")),
                        ),
                );
            }
        }
    }
}

fn forked(turns: Value) -> Value {
    json!({"mode": "forked", "turns": turns})
}

/// *Prepare*: A parent Turn makes two tool calls at once, and only the first one finishes.
/// *Do*: Export the conversation while the second call runs, then finish the second call.
/// *Assert*: The copy keeps the first call's real result, with the same bytes the parent
/// sends next, and closes only the running call with the note.
#[test]
fn conversation_keeps_finished_results_of_a_running_tool_batch() {
    // Prepare
    let mut parent_settings = serde_json::to_value(config()).unwrap();
    parent_settings["settings"]["prompts"]["agent_messages"] =
        json!({"inherited_pending_call": "STILL RUNNING IN THE SOURCE AGENT"});
    let mut parent = SynchronousRuntime::new(serde_json::from_value(parent_settings).unwrap());
    let first = parent.start_turn("parent", "read both files");
    let calls = [
        tool_call("call-a", "read_file", json!({"path":"a.txt"})),
        tool_call("call-b", "read_file", json!({"path":"b.txt"})),
    ];
    let actions = parent
        .complete_with_tool_calls(
            "parent",
            &first,
            &calls,
            [
                runtime_tool("file_system.read_file", json!({"path":"a.txt"})),
                runtime_tool("file_system.read_file", json!({"path":"b.txt"})),
            ],
        )
        .actions();
    let finished = file_read_success(&actions[0], "hello from a");
    parent.apply(
        finished.clone(),
        running("parent")
            .keep(&actions[1])
            .observe_completed_tool_result(&actions[0], &finished),
    );

    // Do
    let seed = messages(&parent.export_conversation(forked(json!("all"))));
    let last = file_read_success(&actions[1], "hello from b");
    let next = parent
        .apply(
            last.clone(),
            running("parent")
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&actions[1], &last),
        )
        .only_action();

    // Assert
    assert_eq!(seed.len(), 4);
    assert_eq!(seed[1], model_assistant_tool_calls(&calls));
    assert_eq!(seed[2]["tool_call_id"], "call-a");
    assert!(seed[2].to_string().contains("hello from a"));
    assert_eq!(
        seed[3],
        model_tool_text("call-b", "read_file", "STILL RUNNING IN THE SOURCE AGENT")
    );
    parent.assert_last_model_message_update(
        &next,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            seed[2].clone(),
            model_tool("call-b", "read_file"),
        ],
    );
}

fn messages(seed: &Value) -> Vec<Value> {
    seed["messages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["message"].clone())
        .collect()
}

/// *Prepare*: Two parent Turns, with steering and an unfinished tool batch in the second, and
/// operator text for calls still running at export.
/// *Do*: Export a recent-Turn seed, restore the parent, and initialize an independent child.
/// *Assert*: The current Turn survives with the running call closed by that text; the older Turn
/// does not.
#[test]
fn conversation_selects_whole_turns_and_closes_unfinished_tool_calls() {
    // Prepare
    let mut parent_settings = serde_json::to_value(config()).unwrap();
    parent_settings["settings"]["prompts"]["agent_messages"] =
        json!({"inherited_pending_call": "STILL RUNNING IN THE SOURCE AGENT"});
    let mut parent = SynchronousRuntime::new(serde_json::from_value(parent_settings).unwrap());
    let first = parent.start_turn("old", "archive code MALACHITE-47");
    parent.finish_turn_with_text("old", &first, "old response");
    let recent = parent
        .apply(
            user_message("recent", "active token COPPER-92", "queue"),
            running("recent")
                .dispatch(llm_call(0))
                .observe(turn_started("recent", "active token COPPER-92")),
        )
        .only_action();
    parent.receive_steering("recent", "same Turn: IRIS-13", &[&recent]);
    let next = parent
        .apply(
            text_completion(&recent, "valid prefix answer"),
            running("recent")
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(
                    "recent",
                    &recent,
                    "valid prefix answer",
                ))
                .observe(turn_steered("recent", "same Turn: IRIS-13")),
        )
        .only_action();
    let pending = parent
        .complete_with_tool_calls(
            "recent",
            &next,
            &[tool_call(
                "unfinished",
                "read_file",
                json!({"path":"notes.txt"}),
            )],
            [runtime_tool(
                "file_system.read_file",
                json!({"path":"notes.txt"}),
            )],
        )
        .only_action();

    // Do
    let seed = parent.export_conversation(forked(json!(1)));
    parent.restart_from_checkpoint();
    let mut child_config = config();
    child_config.task_id = "child".into();
    child_config.system_instructions = "CHILD ONLY INSTRUCTIONS".into();
    let mut child = SynchronousRuntime::new_with_conversation(child_config, seed.clone());
    let child_action = child
        .apply(
            user_message("child-turn", "report context", "queue"),
            running("child-turn")
                .dispatch(llm_call(0))
                .observe(turn_started("child-turn", "report context")),
        )
        .only_action();

    // Assert
    let running_call = [
        model_assistant_tool_calls(&[tool_call(
            "unfinished",
            "read_file",
            json!({"path":"notes.txt"}),
        )]),
        model_tool_text(
            "unfinished",
            "read_file",
            "STILL RUNNING IN THE SOURCE AGENT",
        ),
    ];
    assert_eq!(seed, parent.export_conversation(forked(json!(1))));
    assert_eq!(
        messages(&seed),
        [
            model_user_text("active token COPPER-92"),
            model_assistant_text("valid prefix answer"),
            model_user_text("same Turn: IRIS-13"),
        ]
        .into_iter()
        .chain(running_call.clone())
        .collect::<Vec<_>>()
    );
    assert_eq!(
        parent.export_conversation(json!("fresh")),
        json!({"messages":[]})
    );
    assert_eq!(
        parent.export_conversation(forked(json!("all"))),
        parent.export_conversation(forked(json!(4_294_967_295u32)))
    );
    assert_eq!(
        messages(&parent.export_conversation(forked(json!("all")))).len(),
        7
    );
    child.assert_last_model_message_update(
        &child_action,
        "replace",
        &[
            model_system(),
            model_user_text("active token COPPER-92"),
            model_assistant_text("valid prefix answer"),
            model_user_text("same Turn: IRIS-13"),
            running_call[0].clone(),
            running_call[1].clone(),
            model_user_text("report context"),
        ],
    );
    assert!(child_action.to_string().contains("CHILD ONLY INSTRUCTIONS"));
    assert_eq!(
        parent.inspection_value()["pending_actions"][0]["action_id"],
        pending["action_id"]
    );
    child.finish_turn_with_text("child-turn", &child_action, "independent answer");
    assert!(
        !parent
            .export_conversation(forked(json!("all")))
            .to_string()
            .contains("independent answer")
    );
}

fn compact(runtime: &mut SynchronousRuntime, summary: &str) {
    let action = runtime
        .apply(
            json!({"type":"compact", "extra_instructions":""}),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();
    runtime.apply(
        text_completion(&action, &format!("<summary>{summary}</summary>")),
        idle().observe(json!({
            "type":"context_compacted", "turn_id":null,
            "action_id":action["action_id"], "compaction_id":action["compaction_id"],
            "attempt":1, "trigger":"manual", "summary":summary, "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0},
        })),
    );
}

/// *Prepare*: Two finished Turns, compacted into retained user messages and a summary.
/// *Do*: Select the latest Turn, import everything into a child that compacts again, and
/// run one more parent Turn.
/// *Assert*: The compacted history is one Turn, and later Turns are counted after it.
#[test]
fn conversation_compaction_forms_one_turn_that_later_turns_follow() {
    // Prepare
    let mut parent = SynchronousRuntime::new(config());
    let first = parent.start_turn("old", "archive code MALACHITE-47");
    parent.finish_turn_with_text("old", &first, "discarded assistant details");
    let recent = parent
        .apply(
            user_message("recent", "active token COPPER-92", "queue"),
            running("recent")
                .dispatch(llm_call(0))
                .observe(turn_started("recent", "active token COPPER-92")),
        )
        .only_action();
    parent.finish_turn_with_text("recent", &recent, "recent answer");
    compact(&mut parent, "shared aggregate summary");

    // Do
    parent.restart_from_checkpoint();
    let all = parent.export_conversation(forked(json!("all")));
    let latest = parent.export_conversation(forked(json!(1)));
    let mut child = SynchronousRuntime::new_with_conversation(config(), all.clone());
    compact(&mut child, "new child summary");
    let next = parent
        .apply(
            user_message("next", "follow-up PEARL-5", "queue"),
            running("next")
                .dispatch(llm_call(0))
                .observe(turn_started("next", "follow-up PEARL-5")),
        )
        .only_action();
    parent.finish_turn_with_text("next", &next, "follow-up answer");

    // Assert
    assert_eq!(latest, all);
    assert!(all.to_string().contains("shared aggregate summary"));
    assert!(all.to_string().contains("active token COPPER-92"));
    assert!(!all.to_string().contains("discarded assistant details"));
    let child_seed = child.export_conversation(forked(json!("all")));
    assert!(child_seed.to_string().contains("new child summary"));
    assert!(!child_seed.to_string().contains("shared aggregate summary"));
    assert_eq!(child.export_conversation(forked(json!(1))), child_seed);
    assert_eq!(
        messages(&parent.export_conversation(forked(json!(1)))),
        vec![
            model_user_text("follow-up PEARL-5"),
            model_assistant_text("follow-up answer"),
        ]
    );
    assert_eq!(
        messages(&parent.export_conversation(forked(json!(2)))),
        messages(&parent.export_conversation(forked(json!("all"))))
    );
}

/// *Prepare*: A child imports exchanges without Turn indexes, around one exchange whose
/// index leaves no room for a later Turn.
/// *Do*: Select the latest Turn before and after the child runs its own Turn.
/// *Assert*: Imported exchanges count as Turns, so "1" selects only the latest one.
#[test]
fn conversation_counts_imported_history_as_turns() {
    // Prepare
    let entry = |message: Value, turn_index: Option<u64>| json!({"message":message, "turn_index":turn_index, "source":"history"});
    let seed = json!({"messages":[
        entry(model_user_text("first imported question"), None),
        entry(model_assistant_text("first imported answer"), None),
        entry(model_user_text("second imported question"), None),
        entry(model_assistant_text("second imported answer"), None),
        entry(model_user_text("third imported question"), Some(u64::MAX)),
        entry(model_assistant_text("third imported answer"), Some(u64::MAX)),
        entry(model_user_text("fourth imported question"), None),
        entry(model_assistant_text("fourth imported answer"), None),
    ]});
    let mut child = SynchronousRuntime::new_with_conversation(config(), seed);

    // Do
    let imported_latest = child.export_conversation(forked(json!(1)));
    let imported_two = child.export_conversation(forked(json!(2)));
    let action = child
        .apply(
            user_message("child-turn", "child work", "queue"),
            running("child-turn")
                .dispatch(llm_call(0))
                .observe(turn_started("child-turn", "child work")),
        )
        .only_action();
    child.finish_turn_with_text("child-turn", &action, "child answer");

    // Assert
    assert_eq!(
        messages(&imported_latest),
        vec![
            model_user_text("fourth imported question"),
            model_assistant_text("fourth imported answer"),
        ]
    );
    assert_eq!(
        messages(&imported_two),
        vec![
            model_user_text("third imported question"),
            model_assistant_text("third imported answer"),
            model_user_text("fourth imported question"),
            model_assistant_text("fourth imported answer"),
        ]
    );
    assert_eq!(
        messages(&child.export_conversation(forked(json!(1)))),
        vec![
            model_user_text("child work"),
            model_assistant_text("child answer"),
        ]
    );
    assert_eq!(
        messages(&child.export_conversation(forked(json!(5)))).len(),
        10
    );
    assert_eq!(
        child.export_conversation(forked(json!(5))),
        child.export_conversation(forked(json!("all")))
    );
}

/// *Prepare*: A parent whose starting history includes a system message, then one finished Turn.
/// *Do*: Export all Turns and initialize a child with its own instructions from that seed.
/// *Assert*: The seed has no system message, and the child sees only its own system prompt.
#[test]
fn conversation_fork_excludes_parent_system_messages() {
    // Prepare
    let mut parent = SynchronousRuntime::new_with_history(
        config(),
        vec![
            Message::system_text("PARENT ONLY INSTRUCTIONS"),
            serde_json::from_value(model_user_text("earlier question")).unwrap(),
        ],
    );
    let turn = parent
        .apply(
            user_message("parent-turn", "delegate", "queue"),
            running("parent-turn")
                .dispatch(llm_call(0))
                .observe(turn_started("parent-turn", "delegate")),
        )
        .only_action();
    parent.finish_turn_with_text("parent-turn", &turn, "parent answer");

    // Do
    let seed = parent.export_conversation(forked(json!("all")));
    let mut child_config = config();
    child_config.task_id = "child".into();
    child_config.system_instructions = "CHILD ONLY INSTRUCTIONS".into();
    let mut child = SynchronousRuntime::new_with_conversation(child_config, seed.clone());
    let child_action = child
        .apply(
            user_message("child-turn", "report context", "queue"),
            running("child-turn")
                .dispatch(llm_call(0))
                .observe(turn_started("child-turn", "report context")),
        )
        .only_action();

    // Assert
    assert_eq!(
        messages(&seed),
        vec![
            model_user_text("earlier question"),
            model_user_text("delegate"),
            model_assistant_text("parent answer"),
        ]
    );
    child.assert_last_model_message_update(
        &child_action,
        "replace",
        &[
            model_system(),
            model_user_text("earlier question"),
            model_user_text("delegate"),
            model_assistant_text("parent answer"),
            model_user_text("report context"),
        ],
    );
    let model_input = child_action["model_input"]["messages"]["messages"]
        .as_array()
        .unwrap();
    assert_eq!(
        model_input
            .iter()
            .filter(|message| message["role"] == "system")
            .count(),
        1
    );
    assert!(child_action.to_string().contains("CHILD ONLY INSTRUCTIONS"));
    assert!(
        !child_action
            .to_string()
            .contains("PARENT ONLY INSTRUCTIONS")
    );
}

#[test]
fn preserves_committed_steering_in_partial_fork_when_its_turn_was_interrupted() {
    let mut parent = SynchronousRuntime::new(config());
    let old = parent.start_turn("old", "unrelated request");
    parent.finish_turn_with_text("old", &old, "unrelated answer");
    let recent = parent
        .apply(
            user_message("recent", "use the staging database", "queue"),
            running("recent")
                .dispatch(llm_call(0))
                .observe(turn_started("recent", "use the staging database")),
        )
        .only_action();
    parent.apply(
        user_message("recent", "correction: use the local database", "steer"),
        running("recent")
            .keep(&recent)
            .observe(turn_steering_received(
                "recent",
                "correction: use the local database",
            )),
    );
    parent.apply(
        json!({
            "type": "interrupt",
            "expected_turn_id": "recent",
            "reason": "stop before continuing",
        }),
        interrupted("recent", "stop before continuing")
            .observe(agent_completion_candidate_discarded(
                "recent",
                &recent,
                "interrupt",
            ))
            .observe(turn_interrupted("recent", "stop before continuing"))
            .observe(action_abandoned("recent", &recent, "interrupt")),
    );

    let current = parent
        .apply(
            user_message("current", "delegate the database check", "queue"),
            running("current")
                .dispatch(llm_call(0))
                .observe(turn_started("current", "delegate the database check")),
        )
        .only_action();
    parent.assert_last_model_message_update(
        &current,
        "append",
        &[
            model_user_text("correction: use the local database"),
            model_user_text("delegate the database check"),
        ],
    );
    assert_eq!(
        messages(&parent.export_conversation(forked(json!("all")))),
        vec![
            model_user_text("unrelated request"),
            model_assistant_text("unrelated answer"),
            model_user_text("use the staging database"),
            model_user_text("correction: use the local database"),
            model_user_text("delegate the database check"),
        ],
    );

    let seed = parent.export_conversation(forked(json!(2)));
    let mut child = SynchronousRuntime::new_with_conversation(config(), seed);
    let child_action = child
        .apply(
            user_message("child", "check the database", "queue"),
            running("child")
                .dispatch(llm_call(0))
                .observe(turn_started("child", "check the database")),
        )
        .only_action();
    child.assert_last_model_message_update(
        &child_action,
        "replace",
        &[
            model_system(),
            model_user_text("use the staging database"),
            model_user_text("correction: use the local database"),
            model_user_text("delegate the database check"),
            model_user_text("check the database"),
        ],
    );
}

/// *Prepare*: A finished Turn, then context injected while idle.
/// *Do*: Start the next Turn and select the latest Turn.
/// *Assert*: The context belongs to the Turn it precedes, so "1" keeps it and drops the older Turn.
#[test]
fn conversation_context_injected_before_a_turn_belongs_to_that_turn() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let first = runtime.start_turn("old", "unrelated request");
    runtime.finish_turn_with_text("old", &first, "unrelated answer");
    runtime.apply(
        json!({
            "type": "context_message",
            "content": [{"type": "text", "text": "database for this task: LOCAL-42"}],
        }),
        idle(),
    );

    // Do
    let pending = runtime.export_conversation(forked(json!(1)));
    runtime
        .apply(
            user_message("current", "delegate the database check", "queue"),
            running("current")
                .dispatch(llm_call(0))
                .observe(turn_started("current", "delegate the database check")),
        )
        .only_action();

    // Assert
    assert_eq!(
        messages(&pending),
        vec![model_user_text("database for this task: LOCAL-42")]
    );
    assert_eq!(
        messages(&runtime.export_conversation(forked(json!(1)))),
        vec![
            model_user_text("database for this task: LOCAL-42"),
            model_user_text("delegate the database check"),
        ]
    );
}

/// *Prepare*: A parent whose Core injects an agent-result notification before a Turn that
/// completes a parallel tool batch.
/// *Do*: Export its conversation, import it into a child, and restore the child from a checkpoint.
/// *Assert*: Messages, Turn groups and sources survive each step, and "1" selects only the
/// latest group.
///
/// The injected notification must stay an injection: a child's compaction keeps user requests
/// verbatim, and injected context must not become one.
#[test]
fn preserves_conversation_groups_and_sources_when_imported_and_restored() {
    fn conversation_groups(seed: &Value) -> Vec<Vec<Value>> {
        let entries = seed["messages"].as_array().unwrap();
        let mut groups: Vec<Vec<Value>> = Vec::new();
        let mut previous_index = None;

        for entry in entries {
            let index = entry["turn_index"]
                .as_u64()
                .expect("message has a Turn group");

            if previous_index != Some(index) {
                assert!(previous_index.is_none_or(|previous| index > previous));
                groups.push(Vec::new());
            }

            groups.last_mut().unwrap().push(json!({
                "message": entry["message"],
                "source": entry["source"],
            }));
            previous_index = Some(index);
        }

        groups
    }

    // Prepare
    let mut parent = SynchronousRuntime::new(config());
    let old = parent.start_turn("old", "read the archive");
    parent.finish_turn_with_text("old", &old, "archive requested");
    let notification = json!({
        "id": "archive-result",
        "source": {
            "type": "agent_result",
            "sender_agent_path": "/root/research",
            "termination": {"outcome": "completed", "output": {"archive": "checked"}},
        },
        "level": "info",
        "message": "Archive research completed",
    });
    parent.apply(
        json!({"type": "notification", "notification": notification}),
        completed(
            "old",
            vec![json!({"type": "text", "text": "archive requested"})],
        )
        .observe(json!({"type": "notification_received", "notification": notification})),
    );
    let current = parent
        .apply(
            user_message("current", "read both reports", "queue"),
            running("current")
                .dispatch(llm_call(0))
                .observe(turn_started("current", "read both reports"))
                .observe(notification_delivered("current", notification)),
        )
        .only_action();
    let calls = vec![
        tool_call("call-a", "read_file", json!({"path": "a.txt"})),
        tool_call("call-b", "read_file", json!({"path": "b.txt"})),
    ];
    let actions = parent
        .complete_with_tool_calls(
            "current",
            &current,
            &calls,
            [
                runtime_tool("file_system.read_file", json!({"path": "a.txt"})),
                runtime_tool("file_system.read_file", json!({"path": "b.txt"})),
            ],
        )
        .actions();
    let [first_tool, second_tool]: [Value; 2] = actions.try_into().expect("two tools are running");
    let first_result = text_tool_success(&first_tool, "a contents");
    parent.apply(
        first_result.clone(),
        running("current")
            .keep(&second_tool)
            .observe_completed_tool_result(&first_tool, &first_result),
    );
    let second_result = text_tool_success(&second_tool, "b contents");
    let answer = parent
        .apply(
            second_result.clone(),
            running("current")
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&second_tool, &second_result),
        )
        .only_action();
    parent.finish_turn_with_text("current", &answer, "reports checked");
    let entry = |message: Value, source: &str| json!({"message": message, "source": source});
    let expected_groups = [
        vec![
            entry(model_user_text("read the archive"), "history"),
            entry(model_assistant_text("archive requested"), "history"),
        ],
        vec![
            entry(
                json!({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Agent result from /root/research:\n"},
                        {"type": "text", "text": "Status: completed\nOutput: {\"archive\":\"checked\"}"},
                    ],
                }),
                "injection",
            ),
            entry(model_user_text("read both reports"), "history"),
            entry(model_assistant_tool_calls(&calls), "history"),
            entry(
                model_tool_text("call-a", "read_file", "a contents"),
                "history",
            ),
            entry(
                model_tool_text("call-b", "read_file", "b contents"),
                "history",
            ),
            entry(model_assistant_text("reports checked"), "history"),
        ],
    ];

    // Do
    let seed = parent.export_conversation(forked(json!("all")));
    let mut child = SynchronousRuntime::new_with_conversation(config(), seed.clone());
    let before_restore = child.export_conversation(forked(json!("all")));
    child.restart_from_checkpoint();
    let after_restore = child.export_conversation(forked(json!("all")));

    // Assert
    assert_eq!(conversation_groups(&seed), expected_groups);
    assert_eq!(conversation_groups(&before_restore), expected_groups);
    assert_eq!(conversation_groups(&after_restore), expected_groups);
    assert_eq!(
        conversation_groups(&child.export_conversation(forked(json!(1)))),
        vec![expected_groups[1].clone()]
    );
}
