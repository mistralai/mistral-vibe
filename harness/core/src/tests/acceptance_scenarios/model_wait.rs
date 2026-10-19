use super::*;

/// *Prepare*: A Runtime does not opt into standalone model waiting.
/// *Do*: Open Sessions from explicit-disabled and older omitted availability settings.
/// *Assert*: Neither model catalog advertises wait.
#[test]
fn unsupported_runtimes_do_not_advertise_wait() {
    let mut explicit = config();
    explicit.settings.tools.model_wait = Default::default();
    let mut legacy = serde_json::to_value(&explicit).unwrap();
    let tools = legacy["settings"]["tools"].as_object_mut().unwrap();
    tools.remove("model_wait");
    tools.remove("max_wait_ms");
    for configuration in [explicit, serde_json::from_value(legacy).unwrap()] {
        let mut runtime = SynchronousRuntime::new(configuration);
        let completion = runtime.start_turn("no-wait", "work");
        let tools = completion["model_input"]["tool_catalog"]["tools"]
            .as_array()
            .unwrap();
        assert!(tools.iter().all(|tool| tool["name"] != "wait"));
    }
}

/// *Prepare*: Standalone wait is available directly, never in the programmatic catalog.
/// *Do*: Catch an attempted tools.self.wait call from JavaScript.
/// *Assert*: The program returns the caught error without dispatching a wait Action.
#[test]
fn programmatic_wait_cannot_start_a_waiting_effect() {
    let mut runtime = SynchronousRuntime::new(waiting_config());
    let completion = runtime.start_turn("programmatic-wait", "try waiting");
    let source = r#"async function main() {
      try { await tools.self.wait({timeout_ms: 60_000}); }
      catch (_) { return "not programmatic"; }
      return "must not wait";
    }"#;
    runtime.apply(
        run_typescript_completion(action_id(&completion), "program", source),
        running("programmatic-wait")
            .dispatch(llm_call(1))
            .observe(tool_execution_started("programmatic-wait", "program"))
            .observe_tool_execution_finished(
                "program",
                run_typescript_success(json!("not programmatic")),
            ),
    );
}

/// *Prepare*: The model batches two waits around a valid file read.
/// *Do*: Submit the batch and settle the only dispatched Action.
/// *Assert*: Each wait fails alone, and the read still completes in the original batch order.
#[test]
fn mixed_waits_fail_without_blocking_valid_sibling_tools() {
    let mut runtime = SynchronousRuntime::new(waiting_config());
    let completion = runtime.start_turn("mixed-wait", "read without blocking");
    let calls = vec![
        tool_call("wait-a", "wait", json!({"timeout_ms": 30_000})),
        tool_call("read", "read_file", json!({"path": "valid.txt"})),
        tool_call("wait-b", "wait", json!({"timeout_ms": 30_000})),
    ];
    let read = runtime
        .apply(
            tool_call_completion(action_id(&completion), calls.clone()),
            running("mixed-wait")
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "valid.txt"}),
                ))
                .observe(assistant_tool_calls_committed(
                    "mixed-wait",
                    &completion,
                    &calls,
                ))
                .observe(tool_execution_started("mixed-wait", "wait-a"))
                .observe(tool_execution_started("mixed-wait", "read"))
                .observe(tool_execution_started("mixed-wait", "wait-b"))
                .observe_tool_execution_finished(
                    "wait-a",
                    invalid_tool_call_result("Call wait alone."),
                )
                .observe_tool_execution_finished(
                    "wait-b",
                    invalid_tool_call_result("Call wait alone."),
                ),
        )
        .only_action();
    let result = file_read_success(&read, "read succeeded");
    let completion = runtime
        .apply(
            result.clone(),
            running("mixed-wait")
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&read, &result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &completion,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_invalid_tool_call("wait-a", "wait", "Call wait alone."),
            model_tool_text(
                "read",
                "read_file",
                &result["result"]["structured_content"].to_string(),
            ),
            model_invalid_tool_call("wait-b", "wait", "Call wait alone."),
        ],
    );
}

/// *Prepare*: A model requests one direct wait with an explicit duration.
/// *Do*: Restore the pending Action, then return the ordinary timeout result.
/// *Assert*: Identity and absolute deadline survive, and only the reason reaches the model.
#[test]
fn direct_wait_retains_its_deadline_and_returns_to_the_model() {
    let mut runtime = SynchronousRuntime::new(waiting_config());
    let completion = runtime.start_turn("waiting", "wait for more input");
    let calls = vec![tool_call("wait-1", "wait", json!({"timeout_ms": 30_000}))];
    let wait = runtime
        .apply_at(
            tool_call_completion(action_id(&completion), calls.clone()),
            1_700_000_000_000,
            running("waiting")
                .observe(assistant_tool_calls_committed(
                    "waiting",
                    &completion,
                    &calls,
                ))
                .dispatch(runtime_tool(
                    "self.wait",
                    json!({"timeout_ms": 30_000, "deadline_unix_ms": 1_700_000_030_000_u64}),
                ))
                .observe(tool_execution_started("waiting", "wait-1")),
        )
        .only_action();
    runtime.restart_from_checkpoint();
    let result = structured_tool_success(&wait, json!({"reason": "timeout"}));
    let completion = runtime.apply_at(
        result.clone(),
        1_700_000_030_000,
        running("waiting")
            .dispatch(llm_call(1))
            .observe_tool_result(&wait, &result)
            .observe_tool_execution_finished("wait-1", json!({
                "type": "success", "content": [], "structured_content": {"reason": "timeout"}
            })),
    ).only_action();
    runtime.assert_last_model_message_update(
        &completion,
        "replace",
        &[
            model_system(),
            model_user_text("wait for more input"),
            model_assistant_tool_calls(&calls),
            model_tool_text("wait-1", "wait", "{\"reason\":\"timeout\"}"),
        ],
    );
}

/// *Prepare*: The Runtime supports wait in Sessions with every combination of
/// subagents, background processes, and Host-declared external input.
/// *Do*: Start one Turn in each Session.
/// *Assert*: Only Sessions with at least one of the three offer wait.
#[test]
fn wait_is_offered_only_with_subagents_background_processes_or_external_input() {
    for subagents in [false, true] {
        for background_processes in [false, true] {
            for external_input in [false, true] {
                let mut settings = serde_json::to_value(config()).unwrap();
                let tools = &mut settings["settings"]["tools"];
                tools["model_wait"] = json!({"mode": "enabled", "external_input": external_input});
                tools["subagents"] = feature(subagents);
                tools["background_processes"] = feature(background_processes);
                let mut runtime =
                    SynchronousRuntime::new(serde_json::from_value(settings).unwrap());

                let completion = runtime.start_turn("offer", "work");

                let offered = advertises_wait(&completion);
                assert_eq!(
                    offered,
                    subagents || background_processes || external_input,
                    "subagents={subagents} background_processes={background_processes} external_input={external_input}"
                );
            }
        }
    }
}

/// *Prepare*: The Runtime supports wait, but the Session has no subagents,
/// background processes, or parent, so wait is not offered.
/// *Do*: The model calls wait anyway.
/// *Assert*: The call fails as an unknown tool, no self.wait Action starts, and the Turn continues.
#[test]
fn unoffered_wait_calls_fail_without_starting_a_wait() {
    let mut configuration = waiting_config();
    configuration.settings.tools.subagents =
        serde_json::from_value(json!({"mode": "disabled"})).unwrap();
    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn("hidden-wait", "wait for more input");
    assert!(!advertises_wait(&completion));
    let calls = vec![tool_call("wait-1", "wait", json!({"timeout_ms": 30_000}))];

    runtime.apply(
        tool_call_completion(action_id(&completion), calls.clone()),
        running("hidden-wait")
            .dispatch(llm_call(1))
            .observe(assistant_tool_calls_committed(
                "hidden-wait",
                &completion,
                &calls,
            ))
            .observe(tool_execution_started("hidden-wait", "wait-1"))
            .observe_tool_execution_finished(
                "wait-1",
                invalid_tool_call_result("unsupported top-level tool \"wait\""),
            ),
    );
}

/// *Prepare*: A Session has already published its model tool catalog.
/// *Do*: Change a setting that adds, removes, or reshapes wait between Turns.
/// *Assert*: The next completion replaces the cached tool catalog.
#[test]
fn wait_setting_reconfiguration_replaces_the_tool_catalog() {
    let cases = [
        (
            "enable background processes",
            false,
            "background_processes",
            feature(true),
            true,
        ),
        (
            "disable model wait",
            true,
            "model_wait",
            feature(false),
            false,
        ),
        (
            "change the wait limit",
            true,
            "max_wait_ms",
            json!(60_000),
            true,
        ),
    ];
    for (name, initial_subagents, field, value, offered_after) in cases {
        let mut initial = serde_json::to_value(config()).unwrap();
        initial["settings"]["tools"]["model_wait"] = feature(true);
        initial["settings"]["tools"]["subagents"] = feature(initial_subagents);
        let mut runtime = SynchronousRuntime::new(serde_json::from_value(initial.clone()).unwrap());
        let first = runtime.start_turn("before", "inspect");
        runtime.finish_turn_with_text("before", &first, "done");
        let mut settings = initial["settings"].clone();
        settings["tools"][field] = value;

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

        assert_eq!(
            next["model_input"]["tool_catalog"]["type"], "replace",
            "{name}"
        );
        assert_eq!(advertises_wait(&next), offered_after, "{name}");
    }
}

fn feature(enabled: bool) -> Value {
    json!({"mode": if enabled { "enabled" } else { "disabled" }})
}

fn advertises_wait(completion: &Value) -> bool {
    completion["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .any(|tool| tool["name"] == "wait")
}

fn waiting_config() -> crate::core::testing::HarnessConfig {
    let mut configuration = config();
    configuration.settings.tools.model_wait =
        serde_json::from_value(json!({"mode": "enabled"})).unwrap();
    configuration
}

/// *Prepare*: A direct 30-second wait passes through an always-matching pre-tool Hook.
/// *Do*: The Hook continues with a later `deadline_unix_ms` and a shorter `timeout_ms`.
/// *Assert*: The Runtime receives a deadline no later than the original start plus the effective timeout.
#[test]
fn pre_tool_hook_cannot_extend_the_wait_deadline() {
    let mut configuration = waiting_config();
    configuration.capabilities.hook_bindings = serde_json::from_value(json!([
        {"id": "pre-tool", "point": "pre_tool_call", "order": 0, "selector": {"type": "always"}}
    ]))
    .unwrap();
    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn("waiting", "wait for more input");
    let calls = vec![tool_call("wait-1", "wait", json!({"timeout_ms": 30_000}))];
    let hook = runtime
        .apply_at(
            tool_call_completion(action_id(&completion), calls.clone()),
            1_700_000_000_000,
            running("waiting")
                .observe(assistant_tool_calls_committed(
                    "waiting",
                    &completion,
                    &calls,
                ))
                .dispatch(hook_call("pre_tool_call", &["pre-tool"], Value::Null))
                .observe(tool_execution_started("waiting", "wait-1")),
        )
        .only_action();

    runtime
        .apply_at(
            hook_completed(
                &hook,
                "pre_tool_call",
                json!({
                    "type": "continue",
                    "effective_arguments": {"timeout_ms": 10_000, "deadline_unix_ms": 1_800_000_000_000_u64},
                }),
            ),
            1_700_000_001_000,
            running("waiting").dispatch(runtime_tool(
                "self.wait",
                json!({"timeout_ms": 10_000, "deadline_unix_ms": 1_700_000_010_000_u64}),
            )),
        )
        .only_action();
}
