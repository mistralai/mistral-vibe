use super::*;

/// *Prepare*: The default test configuration omits every built-in feature switch.
/// *Do*: Serialize it and open a Session.
/// *Assert*: No switch is written, and the Session keeps the full legacy tool catalog.
#[test]
fn omitted_builtin_switches_keep_the_legacy_catalog() {
    let configuration = config();
    let tools = serde_json::to_value(&configuration).unwrap()["settings"]["tools"].clone();
    for key in ["file_system", "skills"] {
        assert!(tools.get(key).is_none(), "{key} is serialized");
    }
    for key in ["mode", "sleep"] {
        assert!(
            tools["programmatic"].get(key).is_none(),
            "programmatic.{key} is serialized"
        );
    }

    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn("legacy", "work");

    assert_eq!(
        catalog_names(&completion),
        [
            "search_tool_functions",
            "run_typescript",
            "read_file",
            "write_file",
            "edit",
            "bash"
        ]
    );
}

/// *Prepare*: A Session disables programmatic tool calling, subagents, and command execution.
/// *Do*: Start a Turn, then call `run_typescript` and `search_tool_functions` anyway.
/// *Assert*: Neither tool nor its prompt guidance reaches the model, and both calls fail as
/// unsupported without starting a program or a search.
#[test]
fn disabled_programmatic_tool_calling_removes_its_tools_and_guidance() {
    let configuration = with_tools(
        config(),
        json!({
            "programmatic": {"mode": "disabled"},
            "subagents": {"mode": "disabled"},
        }),
    );
    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn("direct-only", "work");

    assert_eq!(
        catalog_names(&completion),
        ["read_file", "write_file", "edit", "bash"]
    );
    let system = system_text(&completion);
    for absent in [
        "run_typescript",
        "search_tool_functions",
        "### Searchable connector groups",
        "## Current time",
        "discovering tools",
    ] {
        assert!(
            !system.contains(absent),
            "system prompt mentions {absent:?}"
        );
    }
    assert!(system.contains("Find relevant skills with the `skill` tool."));
    assert!(system.contains("before loading skills, reading or searching"));

    let calls = vec![
        tool_call("program", "run_typescript", json!({"code": "1"})),
        tool_call(
            "search",
            "search_tool_functions",
            json!({"mode": "best_match", "query": "files"}),
        ),
    ];
    // The committed assistant message omits `run_typescript` calls by name, as it
    // does when programmatic tool calling is enabled.
    runtime.apply(
        tool_call_completion(action_id(&completion), calls.clone()),
        running("direct-only")
            .dispatch(llm_call(1))
            .observe(assistant_tool_calls_committed(
                "direct-only",
                &completion,
                &calls[1..],
            ))
            .observe(tool_execution_started("direct-only", "program"))
            .observe(tool_execution_started("direct-only", "search"))
            .observe_tool_execution_finished(
                "program",
                invalid_tool_call_result("unsupported top-level tool \"run_typescript\""),
            )
            .observe_tool_execution_finished(
                "search",
                invalid_tool_call_result("unsupported top-level tool \"search_tool_functions\""),
            ),
    );
}

/// *Prepare*: Sessions disable programmatic tool calling next to features that only
/// `run_typescript` can call.
/// *Do*: Create each Session.
/// *Assert*: Creation fails and names the feature that needs programmatic tool calling.
#[test]
fn disabled_programmatic_tool_calling_rejects_programmatic_only_features() {
    let direct_only =
        json!({"programmatic": {"mode": "disabled"}, "subagents": {"mode": "disabled"}});
    let cases = [
        (
            with_tools(config(), json!({"programmatic": {"mode": "disabled"}})),
            "subagents require programmatic tool calling",
        ),
        (
            with_tools(
                config(),
                json!({
                    "programmatic": {"mode": "disabled"},
                    "subagents": {"mode": "disabled"},
                    "background_processes": {"mode": "enabled"},
                }),
            ),
            "background processes require programmatic tool calling",
        ),
        (
            with_tool_group(
                with_tools(config(), direct_only.clone()),
                json!({"name": "calendar", "tools": [{"name": "list", "exposure": "programmatic"}]}),
            ),
            "programmatic-only tool \"calendar.list\" requires programmatic tool calling",
        ),
        (
            with_tool_group(
                with_tools(config(), direct_only.clone()),
                json!({
                    "name": "drive",
                    "metadata": {"type": "connector", "connector_id": "drive"},
                    "tools": [],
                }),
            ),
            "connector tool group \"drive\" requires programmatic tool calling",
        ),
    ];

    for (configuration, expected) in cases {
        let error = HarnessSession::create(configuration)
            .err()
            .expect("programmatic-only features reject session creation");
        assert_eq!(error.to_string(), expected);
    }
}

/// *Prepare*: Programmatic tool calling is disabled, and a provided tool is exposed both
/// directly and programmatically.
/// *Do*: Start a Turn.
/// *Assert*: The direct form reaches the model, and the Session opens.
#[test]
fn disabled_programmatic_tool_calling_keeps_direct_provided_tools() {
    let configuration = with_tool_group(
        with_tools(
            config(),
            json!({"programmatic": {"mode": "disabled"}, "subagents": {"mode": "disabled"}}),
        ),
        json!({
            "name": "calendar",
            "tools": [{"name": "list_events", "exposure": "direct_and_programmatic"}],
        }),
    );
    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn("direct-provided", "work");

    assert_eq!(
        catalog_names(&completion),
        ["read_file", "write_file", "edit", "bash", "list_events"]
    );
}

/// *Prepare*: A Session disables the file system tools and command execution.
/// *Do*: Start a Turn and call `read_file` directly and from `run_typescript`.
/// *Assert*: No file tool reaches the model, and both calls fail without a Runtime Action.
#[test]
fn disabled_file_system_removes_file_tools_from_every_catalog() {
    let configuration = with_tools(
        config(),
        json!({"file_system": {"mode": "disabled"}, "command_environment": {"mode": "disabled"}}),
    );
    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn("no-files", "work");

    assert_eq!(
        catalog_names(&completion),
        ["search_tool_functions", "run_typescript"]
    );
    let calls = vec![tool_call("read", "read_file", json!({"path": "a.txt"}))];
    let next = runtime
        .apply(
            tool_call_completion(action_id(&completion), calls.clone()),
            running("no-files")
                .dispatch(llm_call(1))
                .observe(assistant_tool_calls_committed(
                    "no-files",
                    &completion,
                    &calls,
                ))
                .observe(tool_execution_started("no-files", "read"))
                .observe_tool_execution_finished(
                    "read",
                    invalid_tool_call_result("unsupported top-level tool \"read_file\""),
                ),
        )
        .only_action();
    let source = r#"async function main() {
      try { await tools.file_system.read_file({path: "a.txt"}); }
      catch (_) { return "no file tools"; }
      return "must not read";
    }"#;
    runtime.apply(
        run_typescript_completion(action_id(&next), "program", source),
        running("no-files")
            .dispatch(llm_call(2))
            .observe(tool_execution_started("no-files", "program"))
            .observe_tool_execution_finished(
                "program",
                run_typescript_success(json!("no file tools")),
            ),
    );
}

/// *Prepare*: Sessions disable the file system tools next to features that need them.
/// *Do*: Create each Session.
/// *Assert*: Creation fails and names the dependent feature.
#[test]
fn disabled_file_system_rejects_dependent_features() {
    let cases = [
        (
            with_tools(config(), json!({"file_system": {"mode": "disabled"}})),
            "an enabled command environment requires the file system tools",
        ),
        (
            with_tools(
                config(),
                json!({
                    "file_system": {"mode": "disabled"},
                    "command_environment": {"mode": "disabled"},
                    "large_output": {"mode": "filesystem"},
                }),
            ),
            "the filesystem large-output policy requires the file system tools",
        ),
    ];

    for (configuration, expected) in cases {
        let error = HarnessSession::create(configuration)
            .err()
            .expect("dependent features reject session creation");
        assert_eq!(error.to_string(), expected);
    }
}

/// *Prepare*: A Session enables skills before any skill is configured.
/// *Do*: Start a Turn.
/// *Assert*: The `skill` tool and the skill rules reach the model, so a later catalog
/// change does not change the tool list.
#[test]
fn enabled_skills_advertise_the_skill_tool_without_skills() {
    let configuration = with_tools(config(), json!({"skills": {"mode": "enabled"}}));
    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn("skills-on", "work");

    assert!(catalog_names(&completion).contains(&"skill".to_string()));
    assert!(system_text(&completion).contains("## Available Skills"));
}

/// *Prepare*: A Session disables skills.
/// *Do*: Start a Turn, call `skill` anyway, then create a second Session with a skill.
/// *Assert*: No skill tool or guidance reaches the model, the call fails as unsupported,
/// and a configured skill rejects creation.
#[test]
fn disabled_skills_remove_the_tool_and_reject_configured_skills() {
    let configuration = with_tools(config(), json!({"skills": {"mode": "disabled"}}));
    let mut runtime = SynchronousRuntime::new(configuration.clone());
    let completion = runtime.start_turn("skills-off", "work");

    assert!(!catalog_names(&completion).contains(&"skill".to_string()));
    let system = system_text(&completion);
    assert!(!system.contains("skill"), "system prompt mentions skills");
    assert!(system.contains(
        "Act directly and proactively. Discover additional tools with `search_tool_functions`"
    ));
    let calls = vec![tool_call("load", "skill", json!({"name": "review"}))];
    runtime.apply(
        tool_call_completion(action_id(&completion), calls.clone()),
        running("skills-off")
            .dispatch(llm_call(1))
            .observe(assistant_tool_calls_committed(
                "skills-off",
                &completion,
                &calls,
            ))
            .observe(tool_execution_started("skills-off", "load"))
            .observe_tool_execution_finished(
                "load",
                invalid_tool_call_result("unsupported top-level tool \"skill\""),
            ),
    );

    let mut with_skill = serde_json::to_value(configuration).unwrap();
    with_skill["capabilities"]["skills"] = json!([{
        "name": "review",
        "description": "Review code",
        "path": "/skills/review/SKILL.md",
    }]);
    let error = HarnessSession::create(serde_json::from_value(with_skill).unwrap())
        .err()
        .expect("a configured skill rejects session creation");
    assert_eq!(
        error.to_string(),
        "skills require settings.tools.skills to be enabled"
    );
}

/// *Prepare*: A Session disables programmatic sleep.
/// *Do*: Call `tools.self.sleep` from `run_typescript`.
/// *Assert*: The program catches the missing function, and no sleep Action starts.
#[test]
fn disabled_sleep_removes_self_sleep() {
    let configuration = with_tools(config(), json!({"programmatic": {"sleep": "disabled"}}));
    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn("no-sleep", "work");
    let source = r#"async function main() {
      try { await tools.self.sleep({seconds: 1}); }
      catch (_) { return "no sleep"; }
      return "must not sleep";
    }"#;
    runtime.apply(
        run_typescript_completion(action_id(&completion), "program", source),
        running("no-sleep")
            .dispatch(llm_call(1))
            .observe(tool_execution_started("no-sleep", "program"))
            .observe_tool_execution_finished("program", run_typescript_success(json!("no sleep"))),
    );
}

/// *Prepare*: An idle Session.
/// *Do*: Reconfigure each built-in feature switch.
/// *Assert*: Core rejects every change and keeps its state.
#[test]
fn builtin_switches_cannot_be_reconfigured() {
    let cases = [
        (
            json!({"file_system": {"mode": "disabled"}, "command_environment": {"mode": "disabled"}}),
            "settings.tools.file_system",
        ),
        (
            json!({"skills": {"mode": "enabled"}}),
            "settings.tools.skills",
        ),
        (
            json!({"programmatic": {"mode": "disabled"}, "subagents": {"mode": "disabled"}}),
            "settings.tools.programmatic.mode",
        ),
        (
            json!({"programmatic": {"sleep": "disabled"}}),
            "settings.tools.programmatic.sleep",
        ),
    ];
    for (patch, field) in cases {
        let initial = with_tools(config(), json!({"subagents": {"mode": "disabled"}}));
        let mut runtime = SynchronousRuntime::new(initial.clone());
        let changed = with_tools(initial, patch).settings;

        runtime.reject(
            json!({
                "type": "reconfigure",
                "changes": [{"type": "settings", "value": changed}],
            }),
            json!({
                "code": "invalid_command",
                "error": {
                    "code": "invalid_command",
                    "message": format!("{field} cannot be reconfigured after session creation"),
                    "retryable": false,
                    "details": null,
                },
            }),
        );
    }
}

fn with_tools(configuration: HarnessConfig, patch: Value) -> HarnessConfig {
    let mut value = serde_json::to_value(configuration).unwrap();
    merge(&mut value["settings"]["tools"], patch);
    serde_json::from_value(value).expect("patched settings deserialize")
}

fn with_tool_group(configuration: HarnessConfig, group: Value) -> HarnessConfig {
    let mut value = serde_json::to_value(configuration).unwrap();
    value["capabilities"]["tool_groups"]
        .as_array_mut()
        .expect("capabilities serialize their tool groups")
        .push(group);
    serde_json::from_value(value).expect("patched capabilities deserialize")
}

fn merge(target: &mut Value, patch: Value) {
    match (target, patch) {
        (Value::Object(target), Value::Object(patch)) => {
            for (key, value) in patch {
                merge(target.entry(key).or_insert(Value::Null), value);
            }
        }
        (target, patch) => *target = patch,
    }
}

fn catalog_names(completion: &Value) -> Vec<String> {
    completion["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["name"].as_str().unwrap().to_string())
        .collect()
}

fn system_text(completion: &Value) -> String {
    completion["model_input"]["messages"]["messages"][0]["content"][0]["text"]
        .as_str()
        .expect("the first model message is the generated system prompt")
        .to_string()
}
