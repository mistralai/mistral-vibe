use super::*;
use crate::core::{BackgroundProcessMode, CommandEnvironment};

///
/// *Prepare*: A fresh Core session receives one user message without configured hooks.
/// *Do*: Resolve the single model action with a text completion.
/// *Assert*: Runtime sees the turn start, the assistant message commit, and the completed turn with no tools or retained actions.
///
#[test]
fn plain_completion_ends_one_turn_without_tools() {
    // Prepare
    let turn_id = "turn-plain-completion";
    let mut runtime = SynchronousRuntime::new(config());
    let completion_action = runtime.start_turn(turn_id, "summarize the repository");

    // Do
    runtime.finish_turn_with_text(turn_id, &completion_action, "repository summarized");

    // Assert
    assert_eq!(completion_action["type"], "llm_call");
    assert_eq!(completion_action["iteration"], 0);
}

///
/// *Prepare*: Four sessions select Unix, Git Bash, PowerShell, or in-memory Bash while background processes are enabled.
/// *Do*: Inspect the first model request, dispatch the selected command directly, restore it, then dispatch the same command through `run_typescript` and restore again.
/// *Assert*: Each environment owns its model-facing name and guidance while every foreground command keeps the `file_system.bash` Runtime route across restoration.
///
#[test]
fn command_environments_change_model_tools_without_changing_the_runtime_route() {
    // Prepare
    let cases = [
        (
            CommandEnvironment::Unix,
            "bash",
            "Run a shell command and capture stdout",
        ),
        (
            CommandEnvironment::GitBash,
            "bash",
            "run a one-off Git Bash command",
        ),
        (
            CommandEnvironment::PowerShell,
            "bash",
            "run a one-off PowerShell command",
        ),
        (
            CommandEnvironment::InMemoryBash,
            "bash",
            "lightweight virtual filesystem environment",
        ),
    ];

    for (index, (environment, tool_name, description)) in cases.into_iter().enumerate() {
        let turn_id = format!("turn-command-environment-{index}");
        let command = if environment == CommandEnvironment::PowerShell {
            "Get-Location"
        } else {
            "pwd"
        };
        let arguments = json!({"command": command, "timeout_seconds": 5});
        let result = json!({
            "command": command,
            "stdout": "/workspace\n",
            "stderr": "",
            "returncode": 0,
            "was_truncated": false,
        });
        let mut harness_config = config();
        harness_config.settings.tools.command_environment = environment;
        harness_config.settings.tools.background_processes = BackgroundProcessMode::Enabled;
        let mut runtime = SynchronousRuntime::new(harness_config);
        let first_completion = runtime.start_turn(&turn_id, "run a command twice");

        // Do
        let direct_calls = vec![tool_call("direct-command", tool_name, arguments.clone())];
        let direct_action = runtime
            .complete_with_tool_calls(
                &turn_id,
                &first_completion,
                &direct_calls,
                [runtime_tool("file_system.bash", arguments.clone())],
            )
            .only_action();
        runtime.restart_from_checkpoint();
        let direct_result = structured_tool_success(&direct_action, result.clone());
        let second_completion = runtime
            .apply(
                direct_result.clone(),
                running(&turn_id)
                    .dispatch(llm_call(1))
                    .observe_completed_tool_result(&direct_action, &direct_result),
            )
            .only_action();
        let source = format!(
            "async function main() {{ return tools.file_system.{tool_name}({{ command: {command:?}, timeout_seconds: 5 }}); }}"
        );
        let program_action = runtime
            .complete_with_typescript_program(
                &turn_id,
                &second_completion,
                "program-command",
                &source,
                [runtime_tool("file_system.bash", arguments)],
            )
            .only_action();
        runtime.restart_from_checkpoint();
        let outer_program_result = run_typescript_success(result.clone());
        let program_result = structured_tool_success(&program_action, result);
        let final_completion = runtime
            .apply(
                program_result.clone(),
                running(&turn_id)
                    .dispatch(llm_call(2))
                    .observe_tool_result(&program_action, &program_result)
                    .observe_tool_execution_finished("program-command", outer_program_result),
            )
            .only_action();
        runtime.finish_turn_with_text(&turn_id, &final_completion, "commands completed");

        // Assert
        let catalog = first_completion["model_input"]["tool_catalog"]["tools"]
            .as_array()
            .expect("first completion replaces the direct tool catalog");
        let selected_tool = catalog
            .iter()
            .find(|tool| tool["name"] == tool_name)
            .expect("selected command tool is direct");
        assert!(
            selected_tool["description"]
                .as_str()
                .unwrap()
                .contains(description)
        );
        assert!(
            catalog.iter().all(|tool| {
                !matches!(
                    tool["name"].as_str(),
                    Some(name) if name != tool_name
                        && matches!(name, "bash" | "git_bash" | "powershell")
                )
            }),
            "only the selected command tool is exposed"
        );
        let system_message =
            serde_json::to_string(&first_completion["model_input"]["messages"]["messages"][0])
                .unwrap();
        assert!(system_message.contains("accepts the same command syntax as"));
        assert!(system_message.contains(&format!("tools.file_system.{tool_name}")));
        assert_eq!(direct_action["call"]["name"], "file_system.bash");
        assert_eq!(program_action["call"]["name"], "file_system.bash");
    }
}

///
/// *Prepare*: A session disables command execution while ordinary filesystem tools remain available.
/// *Do*: Start a turn, restore from its checkpoint, finish the turn, then reconfigure to Unix and start another turn.
/// *Assert*: Disabled sessions expose no foreground or background command tools, restoration preserves that catalog, and reconfiguration installs the Unix command catalog.
///
#[test]
fn disabled_command_environment_removes_commands_across_restore_and_reconfiguration() {
    // Prepare
    let mut harness_config = config();
    harness_config.settings.tools.command_environment = CommandEnvironment::Disabled;
    let mut runtime = SynchronousRuntime::new(harness_config.clone());
    let disabled_completion = runtime.start_turn("turn-disabled-command", "inspect files safely");

    // Do
    runtime.restart_from_checkpoint();
    let restored_completion = runtime
        .apply(
            json!({
                "type": "completion_model_input_resync_requested",
                "action_id": action_id(&disabled_completion),
            }),
            running("turn-disabled-command").refresh(llm_call(0)),
        )
        .only_action();
    runtime.finish_turn_with_text(
        "turn-disabled-command",
        &restored_completion,
        "files inspected",
    );
    let mut unix_settings = harness_config.settings;
    unix_settings.tools.command_environment = CommandEnvironment::Unix;
    runtime.reconfigure_settings(
        unix_settings,
        completed(
            "turn-disabled-command",
            vec![json!({"type": "text", "text": "files inspected"})],
        ),
    );
    let unix_completion = runtime
        .apply(
            user_message("turn-unix-command", "run pwd", "queue"),
            running("turn-unix-command")
                .dispatch(llm_call(0))
                .observe(turn_started("turn-unix-command", "run pwd")),
        )
        .only_action();

    // Assert
    for completion in [&disabled_completion, &restored_completion] {
        let catalog = completion["model_input"]["tool_catalog"]["tools"]
            .as_array()
            .expect("disabled completion replaces the direct tool catalog");
        assert!(catalog.iter().all(|tool| {
            !matches!(
                tool["name"].as_str(),
                Some("bash" | "git_bash" | "powershell")
            )
        }));
        let model_input = serde_json::to_string(&completion["model_input"]).unwrap();
        assert!(!model_input.contains("tools.file_system.bash"));
        assert!(!model_input.contains("tools.process.start"));
        assert!(model_input.contains("- file_system"));
    }
    let unix_catalog = unix_completion["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .expect("Unix completion replaces the direct tool catalog");
    assert!(unix_catalog.iter().any(|tool| tool["name"] == "bash"));
    assert!(
        serde_json::to_string(&unix_completion["model_input"])
            .unwrap()
            .contains("- file_system")
    );
}

///
/// *Prepare*: A Unix session checkpoints one pending Bash Action before the Runtime reports its result.
/// *Do*: Restore the checkpoint with command execution disabled, then deliver the saved Bash result.
/// *Assert*: Core accepts the already-owned result but removes command tools and guidance from the next model request.
///
#[test]
fn restoring_with_commands_disabled_accepts_an_existing_command_result() {
    // Prepare
    let turn_id = "turn-disable-command-on-restore";
    let mut unix_config = config();
    unix_config.settings.tools.background_processes = BackgroundProcessMode::Disabled;
    let mut runtime = SynchronousRuntime::new(unix_config.clone());
    let first_completion = runtime.start_turn(turn_id, "run pwd once");
    let arguments = json!({"command": "pwd", "timeout_seconds": 5});
    let direct_calls = vec![tool_call(
        "command-before-restore",
        "bash",
        arguments.clone(),
    )];
    let command_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &direct_calls,
            [runtime_tool("file_system.bash", arguments)],
        )
        .only_action();
    let mut disabled_config = unix_config;
    disabled_config.settings.tools.command_environment = CommandEnvironment::Disabled;

    // Do
    runtime.restart_from_checkpoint_with_config(disabled_config);
    let command_result = structured_tool_success(
        &command_action,
        json!({
            "command": "pwd",
            "stdout": "/workspace\n",
            "stderr": "",
            "returncode": 0,
            "was_truncated": false,
        }),
    );
    let next_completion = runtime
        .apply(
            command_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&command_action, &command_result),
        )
        .only_action();

    // Assert
    let catalog = next_completion["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .expect("restoration replaces the direct tool catalog");
    assert!(catalog.iter().all(|tool| {
        !matches!(
            tool["name"].as_str(),
            Some("bash" | "git_bash" | "powershell")
        )
    }));
    let model_input = serde_json::to_string(&next_completion["model_input"]).unwrap();
    assert!(!model_input.contains("execute commands in the runtime filesystem"));
}

///
/// *Prepare*: A fresh Core session receives one user message without configured hooks.
/// *Do*: Resolve the model action with a reasoning-only completion and a stop finish reason.
/// *Assert*: Core commits the reasoning candidate and completes the turn with empty visible output.
///
#[test]
fn reasoning_only_completion_ends_the_turn_with_empty_visible_output() {
    // Prepare
    let turn_id = "turn-reasoning-only-completion";
    let reasoning = "The request is complete.";
    let mut runtime = SynchronousRuntime::new(config());
    let completion_action = runtime.start_turn(turn_id, "finish after reasoning");
    let candidate = json!({
        "message": {
            "role": "assistant",
            "content": [{
                "type": "reasoning",
                "content": [{"type": "text", "text": reasoning}],
            }],
        },
        "finish_reason": "stop",
        "usage": null,
    });

    // Do
    let transition = runtime.apply(
        json!({
            "type": "completion_succeeded",
            "action_id": action_id(&completion_action),
            "result": {
                "parts": [{
                    "type": "reasoning",
                    "content": [{"type": "text", "text": reasoning}],
                }],
                "finish_reason": "stop",
                "usage": null,
            },
        }),
        completed(turn_id, vec![])
            .observe(json!({
                "type": "assistant_message_committed",
                "turn_id": turn_id,
                "action_id": action_id(&completion_action),
                "candidate": candidate,
            }))
            .observe(json!({
                "type": "turn_completed",
                "turn_id": turn_id,
                "output": [],
            })),
    );

    // Assert
    assert!(transition.actions().is_empty());
}

///
/// *Prepare*: A session exposes two programmatic tools and receives a discovery request with an unknown field.
/// *Do*: Complete the first model call with that best-match discovery request.
/// *Assert*: Core ignores the unknown field and reports the discovery summary before requesting the next completion.
///
#[test]
fn tool_discovery_reports_the_completed_summary() {
    // Prepare
    let turn_id = "turn-tool-discovery";
    let mut harness_config = config();
    harness_config.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "github",
        "description": "GitHub tools",
        "tools": [
            {
                "name": "search_issues",
                "description": "Search GitHub issues",
                "input_schema": {"type": "object"},
                "exposure": "programmatic",
            },
            {
                "name": "search_pull_requests",
                "description": "Search GitHub pull requests",
                "input_schema": {"type": "object"},
                "exposure": "programmatic",
            },
        ],
    }]))
    .expect("tool discovery configuration is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "find GitHub work");
    let discovery_calls = vec![tool_call(
        "discovery-1",
        "search_tool_functions",
        json!({
            "mode": "best_match",
            "query": "search GitHub",
            "connectors": ["github"],
            "ignored": {"future": true},
        }),
    )];
    let discovery_result = "---\nmode: best_match\nrelevantFunctionCount: 2\nrelatedFunctionCount: 2\nrelatedConnectorCount: 1\nrelevantConnectorNoticeCount: 1\n---\n\n# Best Matching Tool Candidates\n\n1. github.search_issues - 1.9\n2. github.search_pull_requests - 1.8\n\n# Connector Guidance\n\n<connector-guidance>\n  <connector>\n    <name>github</name>\n    <status>connected</status>\n    <guidance>GitHub tools</guidance>\n  </connector>\n</connector-guidance>";

    // Do
    let second_completion = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), discovery_calls.clone()),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &discovery_calls,
                ))
                .observe(tool_execution_started(turn_id, "discovery-1"))
                .observe(json!({
                    "type": "tool_discovery_finished",
                    "turn_id": turn_id,
                    "call_id": "discovery-1",
                    "summary": {
                        "kind": "best_match",
                        "tool_count": 2,
                        "connector_notice_count": 0,
                        "group_namespaces": ["github"],
                    },
                }))
                .observe(tool_execution_finished(
                    turn_id,
                    "discovery-1",
                    json!({
                        "type": "success",
                        "content": [{"type": "text", "text": discovery_result}],
                    }),
                )),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &second_completion, "GitHub tools found");

    // Assert
    assert_eq!(second_completion["iteration"], 1);
}

///
/// *Prepare*: A session exposes 22 tools whose descriptions share one unique query token.
/// *Do*: Complete the first model call with a best-match discovery request matching every tool.
/// *Assert*: The formatter reports 22 related matches and 20 returned tools, while Core observes the returned count.
///
#[test]
fn tool_discovery_reports_the_returned_count_when_matches_exceed_the_limit() {
    // Prepare
    const MATCHING_TOOL_COUNT: usize = 22;
    const RETURNED_TOOL_COUNT: usize = 20;

    let turn_id = "turn-tool-discovery-over-limit";
    let mut harness_config = config();
    let tools = (1..=MATCHING_TOOL_COUNT)
        .map(|index| {
            json!({
                "name": format!("search_issues_{index}"),
                "description": format!("contractcountunique GitHub issue search {index}"),
                "input_schema": {"type": "object"},
                "exposure": "programmatic",
            })
        })
        .collect::<Vec<_>>();
    harness_config.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "github",
        "description": "GitHub tools",
        "tools": tools,
    }]))
    .expect("tool discovery configuration is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "find matching tools");
    let discovery_calls = vec![tool_call(
        "discovery-over-limit",
        "search_tool_functions",
        json!({
            "mode": "best_match",
            "query": "contractcountunique",
            "connectors": ["github"],
        }),
    )];
    let discovery_result = "---\nmode: best_match\nrelevantFunctionCount: 20\nrelatedFunctionCount: 22\nrelatedConnectorCount: 1\nrelevantConnectorNoticeCount: 1\n---\n\n# Best Matching Tool Candidates\n\nmoreCandidatesAvailable: true\n\n1. github.search_issues_1 - 0.1\n2. github.search_issues_10 - 0.1\n3. github.search_issues_11 - 0.1\n4. github.search_issues_12 - 0.1\n5. github.search_issues_13 - 0.1\n6. github.search_issues_14 - 0.1\n7. github.search_issues_15 - 0.1\n8. github.search_issues_16 - 0.1\n9. github.search_issues_17 - 0.1\n10. github.search_issues_18 - 0.1\n11. github.search_issues_19 - 0.1\n12. github.search_issues_2 - 0.1\n13. github.search_issues_20 - 0.1\n14. github.search_issues_21 - 0.1\n15. github.search_issues_22 - 0.1\n16. github.search_issues_3 - 0.1\n17. github.search_issues_4 - 0.1\n18. github.search_issues_5 - 0.1\n19. github.search_issues_6 - 0.1\n20. github.search_issues_7 - 0.1\n\n# Connector Guidance\n\n<connector-guidance>\n  <connector>\n    <name>github</name>\n    <status>connected</status>\n    <guidance>GitHub tools</guidance>\n  </connector>\n</connector-guidance>";

    // Do
    let second_completion = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), discovery_calls.clone()),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &discovery_calls,
                ))
                .observe(tool_execution_started(turn_id, "discovery-over-limit"))
                .observe(json!({
                    "type": "tool_discovery_finished",
                    "turn_id": turn_id,
                    "call_id": "discovery-over-limit",
                    "summary": {
                        "kind": "best_match",
                        "tool_count": RETURNED_TOOL_COUNT,
                        "connector_notice_count": 0,
                        "group_namespaces": ["github"],
                    },
                }))
                .observe(tool_execution_finished(
                    turn_id,
                    "discovery-over-limit",
                    json!({
                        "type": "success",
                        "content": [{"type": "text", "text": discovery_result}],
                    }),
                )),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &second_completion, "matching tools found");

    // Assert
    let tool_result_text = second_completion["model_input"]["messages"]["messages"]
        .as_array()
        .and_then(|messages| messages.last())
        .and_then(|message| message["content"][0]["text"].as_str())
        .expect("the discovery result is appended to the next model request");
    assert!(tool_result_text.contains("relevantFunctionCount: 20"));
    assert!(tool_result_text.contains("relatedFunctionCount: 22"));
    assert_eq!(second_completion["iteration"], 1);
}

///
/// *Prepare*: A Runtime owns one pending completion and then loses both model-input caches.
/// *Do*: Request a full model-input resynchronization and complete the refreshed Action.
/// *Assert*: Core refreshes the same Action with complete replacement payloads and does not change the turn.
///
#[test]
fn model_input_resync_refreshes_the_runtime_caches_without_restarting_work() {
    // Prepare
    let turn_id = "turn-model-input-resync";
    let mut runtime = SynchronousRuntime::new(config());
    let original_completion = runtime.start_turn(turn_id, "recover the model input");

    // Do
    runtime.lose_model_input_cache();
    let refreshed_completion = runtime
        .apply(
            json!({
                "type": "completion_model_input_resync_requested",
                "action_id": action_id(&original_completion),
            }),
            running(turn_id).refresh(json!({
                "type": "llm_call",
                "action_id": action_id(&original_completion),
                "turn_id": turn_id,
                "purpose": "agent",
                "iteration": 0,
                "model_input": {
                    "messages": {"type": "replace"},
                    "tool_catalog": {"type": "replace"},
                },
            })),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &refreshed_completion,
        "replace",
        &[model_system(), model_user_text("recover the model input")],
    );
    runtime.finish_turn_with_text(turn_id, &refreshed_completion, "model input recovered");

    // Assert
    assert_eq!(
        action_id(&refreshed_completion),
        action_id(&original_completion)
    );
    assert_eq!(
        refreshed_completion["model_input"]["messages"]["messages"],
        original_completion["model_input"]["messages"]["messages"],
    );
    assert_eq!(
        refreshed_completion["model_input"]["tool_catalog"]["tools"],
        original_completion["model_input"]["tool_catalog"]["tools"],
    );
}

///
/// *Prepare*: A turn's first completion contains one TypeScript program with three parallel filesystem calls.
/// *Do*: Return all three Runtime results, then complete the follow-up model call.
/// *Assert*: Every parallel action is dispatched or retained explicitly, every tool result is observed, and the final completion ends the turn.
///
#[test]
fn three_programmatic_tool_calls_resume_the_model_and_complete_the_turn() {
    // Prepare
    let turn_id = "turn-three-programmatic-tools";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "read three files");
    let source = "async function main() { const [first, second, third] = await Promise.all([tools.file_system.read_file({ path: 'first.txt' }), tools.file_system.read_file({ path: 'second.txt' }), tools.file_system.read_file({ path: 'third.txt' })]); return { first: first.content, second: second.content, third: third.content }; }";

    // Do
    let tool_actions = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            "program-three-reads",
            source,
            [
                runtime_tool("file_system.read_file", json!({"path": "first.txt"})),
                runtime_tool("file_system.read_file", json!({"path": "second.txt"})),
                runtime_tool("file_system.read_file", json!({"path": "third.txt"})),
            ],
        )
        .actions();
    let [first_tool, second_tool, third_tool]: [Value; 3] = tool_actions
        .try_into()
        .expect("program dispatches three tool actions");

    let first_result = file_read_success(&first_tool, "first");
    runtime.apply(
        first_result.clone(),
        running(turn_id)
            .keep(&second_tool)
            .keep(&third_tool)
            .observe_tool_result(&first_tool, &first_result),
    );
    let second_result = file_read_success(&second_tool, "second");
    runtime.apply(
        second_result.clone(),
        running(turn_id)
            .keep(&third_tool)
            .observe_tool_result(&second_tool, &second_result),
    );
    let third_result = file_read_success(&third_tool, "third");
    let outer_result = run_typescript_success(json!({
        "first": "first",
        "second": "second",
        "third": "third",
    }));
    let final_completion = runtime
        .apply(
            third_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&third_tool, &third_result)
                .observe_tool_execution_finished("program-three-reads", outer_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &final_completion,
        "append",
        &[
            model_assistant_typescript("program-three-reads", source),
            model_tool_text(
                "program-three-reads",
                "run_typescript",
                r#"{"first":"first","second":"second","third":"third"}"#,
            ),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &final_completion, "all three files were read");

    // Assert
    assert_eq!(first_tool["call"]["arguments"]["path"], "first.txt");
    assert_eq!(second_tool["call"]["arguments"]["path"], "second.txt");
    assert_eq!(third_tool["call"]["arguments"]["path"], "third.txt");
}

///
/// *Prepare*: A TypeScript program constructs a filesystem write argument with a lone UTF-16 surrogate.
/// *Do*: Submit the program through the serialized Core interface.
/// *Assert*: Core completes `run_typescript` with a non-retryable serialization failure and dispatches no filesystem Action.
///
#[test]
fn invalid_unicode_in_programmatic_arguments_finishes_without_a_runtime_tool_action() {
    // Prepare
    let turn_id = "turn-invalid-program-unicode";
    let call_id = "program-invalid-unicode";
    let source = "async function main() { return tools.file_system.write_file({ path: 'navigation.yml', content: String.fromCharCode(0xd800) }); }";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "write the navigation file");
    let error = json!({
        "name": "SerializationError",
        "message": "TypeScript isolate result is not valid JSON; values must contain valid Unicode",
    });
    let failure_text = format!(
        "run_typescript failed: {}",
        serde_json::to_string(&error).expect("serialization error serializes")
    );
    let failure_result = json!({
        "type": "failure",
        "content": [{"type": "text", "text": failure_text}],
        "error": {
            "code": "SerializationError",
            "message": error["message"],
            "retryable": false,
            "details": null,
        },
    });

    // Do
    let next_completion = runtime
        .apply(
            run_typescript_completion(action_id(&first_completion), call_id, source),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_execution_started(turn_id, call_id))
                .observe(tool_execution_finished(turn_id, call_id, failure_result)),
        )
        .only_action();

    // Assert
    assert_eq!(next_completion["type"], "llm_call");
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            json!({
                "role": "tool",
                "tool_call_id": call_id,
                "name": "run_typescript",
                "outcome": "failure",
                "content": [{"type": "text", "text": failure_text}],
            }),
        ],
    );
}

///
/// *Prepare*: A TypeScript program selects results from two consecutive pairs of filesystem calls
/// with `Promise.race()` and `Promise.any()`.
/// *Do*: Complete each pair one result at a time, then complete the follow-up model call.
/// *Assert*: Core keeps each pending sibling, dispatches the second batch only after the first batch
/// settles, observes every result in order, and resumes the model with the selected values.
///
#[test]
fn promise_selection_waits_for_each_pending_programmatic_batch() {
    // Prepare
    let turn_id = "turn-programmatic-promise-selection";
    let call_id = "program-promise-selection";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "select results from two tool batches");
    let source = "async function main() { const raced = await Promise.race([tools.file_system.read_file({ path: 'race-first.txt' }), tools.file_system.read_file({ path: 'race-second.txt' })]); const any = await Promise.any([tools.file_system.read_file({ path: 'any-first.txt' }), tools.file_system.read_file({ path: 'any-second.txt' })]); return { any: any.content, race: raced.content }; }";

    // Do
    let race_actions = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [
                runtime_tool("file_system.read_file", json!({"path": "race-first.txt"})),
                runtime_tool("file_system.read_file", json!({"path": "race-second.txt"})),
            ],
        )
        .actions();
    let [race_first, race_second]: [Value; 2] = race_actions
        .try_into()
        .expect("Promise.race dispatches both tool actions");

    let race_first_result = file_read_success(&race_first, "race first");
    runtime.apply(
        race_first_result.clone(),
        running(turn_id)
            .keep(&race_second)
            .observe_tool_result(&race_first, &race_first_result),
    );

    let race_second_result = file_read_success(&race_second, "race second");
    let any_actions = runtime
        .apply(
            race_second_result.clone(),
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "any-first.txt"}),
                ))
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "any-second.txt"}),
                ))
                .observe_tool_result(&race_second, &race_second_result),
        )
        .actions();
    let [any_first, any_second]: [Value; 2] = any_actions
        .try_into()
        .expect("Promise.any dispatches both tool actions");

    let any_first_result = file_read_success(&any_first, "any first");
    runtime.apply(
        any_first_result.clone(),
        running(turn_id)
            .keep(&any_second)
            .observe_tool_result(&any_first, &any_first_result),
    );

    let any_second_result = file_read_success(&any_second, "any second");
    let outer_result = run_typescript_success(json!({
        "any": "any first",
        "race": "race first",
    }));
    let final_completion = runtime
        .apply(
            any_second_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&any_second, &any_second_result)
                .observe_tool_execution_finished(call_id, outer_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &final_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(
                call_id,
                "run_typescript",
                r#"{"any":"any first","race":"race first"}"#,
            ),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &final_completion, "selected both results");

    // Assert
    assert_eq!(race_first["call"]["arguments"]["path"], "race-first.txt");
    assert_eq!(race_second["call"]["arguments"]["path"], "race-second.txt");
    assert_eq!(any_first["call"]["arguments"]["path"], "any-first.txt");
    assert_eq!(any_second["call"]["arguments"]["path"], "any-second.txt");
}

///
/// *Prepare*: One model completion starts two `run_typescript` executions; the first completes
/// locally while the second waits on a provided callback-style tool Action.
/// *Do*: Observe the initial batch, restore its checkpoint, and complete the remaining Action.
/// *Assert*: Core reports the first wrapper as finished before the batch barrier and does not
/// replay that terminal Observation after restoration.
///
#[test]
fn completed_programmatic_sibling_is_observed_before_another_sibling_resumes() {
    // Prepare
    let turn_id = "turn-parallel-programs";
    let completed_call_id = "program-completed";
    let pending_call_id = "program-awaiting-callback";
    let completed_source = "async function main() { return { status: 'done' }; }";
    let pending_source = "async function main() { return tools.auth.ask_enable_connector({ connector: 'data_gouv' }); }";
    let mut harness_config = config();
    harness_config.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "auth",
        "description": "Connector authorization callbacks",
        "tools": [{
            "name": "ask_enable_connector",
            "description": "Ask the user to enable a connector",
            "input_schema": {
                "type": "object",
                "properties": {"connector": {"type": "string"}},
                "required": ["connector"],
                "additionalProperties": false,
            },
            "exposure": "programmatic",
        }],
    }]))
    .expect("callback tool configuration is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "finish one task and request authorization");
    let calls = vec![
        tool_call(
            completed_call_id,
            "run_typescript",
            json!({"code": completed_source}),
        ),
        tool_call(
            pending_call_id,
            "run_typescript",
            json!({"code": pending_source}),
        ),
    ];

    // Do
    let callback_action = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls),
            running(turn_id)
                .dispatch(json!({
                    "type": "provided_tool_call",
                    "call": {
                        "type": "provided",
                        "group_name": "auth",
                        "tool_name": "ask_enable_connector",
                        "arguments": {"connector": "data_gouv"},
                    },
                }))
                .observe(tool_execution_started(turn_id, completed_call_id))
                .observe(tool_execution_started(turn_id, pending_call_id))
                .observe(tool_execution_finished(
                    turn_id,
                    completed_call_id,
                    run_typescript_success(json!({"status": "done"})),
                )),
        )
        .only_action();
    runtime.restart_from_checkpoint();
    let callback_result = structured_tool_success(
        &callback_action,
        json!({"connector": "data_gouv", "enabled": true}),
    );
    let next_completion = runtime
        .apply(
            callback_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&callback_action, &callback_result)
                .observe_tool_execution_finished(
                    pending_call_id,
                    run_typescript_success(json!({
                        "connector": "data_gouv",
                        "enabled": true,
                    })),
                ),
        )
        .only_action();

    // Assert
    assert_eq!(callback_action["call"]["tool_name"], "ask_enable_connector");
    assert_eq!(next_completion["type"], "llm_call");
}

///
/// *Prepare*: One turn starts with a model completion that requests two direct tools.
/// *Do*: Resolve both tools, request and resolve a third tool from the next completion, then return a final answer.
/// *Assert*: Each tool round has the exact pending-action set and observations before the third completion ends the turn.
///
#[test]
fn multiple_completion_and_tool_rounds_complete_one_turn() {
    // Prepare
    let turn_id = "turn-multiple-tool-rounds";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "inspect three files");
    let first_calls = vec![
        tool_call("direct-first", "read_file", json!({"path": "first.txt"})),
        tool_call("direct-second", "read_file", json!({"path": "second.txt"})),
    ];

    // Do
    let first_round_actions = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &first_calls,
            [
                runtime_tool("file_system.read_file", json!({"path": "first.txt"})),
                runtime_tool("file_system.read_file", json!({"path": "second.txt"})),
            ],
        )
        .actions();
    let [first_tool, second_tool]: [Value; 2] = first_round_actions
        .try_into()
        .expect("first round dispatches two tools");
    let first_result = text_tool_success(&first_tool, "first contents");
    runtime.apply(
        first_result.clone(),
        running(turn_id)
            .keep(&second_tool)
            .observe_completed_tool_result(&first_tool, &first_result),
    );
    let second_result = text_tool_success(&second_tool, "second contents");
    let second_completion = runtime
        .apply(
            second_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&second_tool, &second_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &second_completion,
        "append",
        &[
            model_assistant_tool_calls(&first_calls),
            model_tool_text("direct-first", "read_file", "first contents"),
            model_tool_text("direct-second", "read_file", "second contents"),
        ],
    );
    let second_calls = vec![tool_call(
        "direct-third",
        "read_file",
        json!({"path": "third.txt"}),
    )];
    let third_tool = runtime
        .complete_with_tool_calls(
            turn_id,
            &second_completion,
            &second_calls,
            [runtime_tool(
                "file_system.read_file",
                json!({"path": "third.txt"}),
            )],
        )
        .only_action();
    let third_result = text_tool_success(&third_tool, "third contents");
    let final_completion = runtime
        .apply(
            third_result.clone(),
            running(turn_id)
                .dispatch(llm_call(2))
                .observe_completed_tool_result(&third_tool, &third_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &final_completion,
        "append",
        &[
            model_assistant_tool_calls(&second_calls),
            model_tool_text("direct-third", "read_file", "third contents"),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &final_completion, "inspection complete");

    // Assert
    assert_eq!(second_completion["iteration"], 1);
    assert_eq!(third_tool["call"]["arguments"]["path"], "third.txt");
    assert_eq!(final_completion["iteration"], 2);
}
