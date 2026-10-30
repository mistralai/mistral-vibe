use super::*;
use crate::core::InputModality;

const READ_FILE_TEXT_DESCRIPTION: &str = "Read a text file with optional zero-indexed line offset and limit. When `was_truncated` is true, read on from `next_offset`. An offset past the end of the file is an error. A directory reads as a listing of its entries, one per line with `/` after each subdirectory, at most 500 at a time.";

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
        // No skills are configured, so the Session exposes no `skill` tool.
        "`skill` tool",
        "loading skills",
    ] {
        assert!(
            !system.contains(absent),
            "system prompt mentions {absent:?}"
        );
    }
    assert!(system.contains("before reading or searching"));

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
        ["list_events", "read_file", "write_file", "edit", "bash"]
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

/// *Prepare*: A Session declares extracted document text and native `read_file` images while its
/// agent model reads images, and finishes a Turn.
/// *Do*: Reconfigure it for a model without image input, keeping the declaration, and start a
/// Turn.
/// *Assert*: The first `read_file` describes the image itself; after the switch, Core replaces
/// the model tool catalog and leaves the image sentence out, with no new declaration.
#[test]
fn native_read_file_images_are_described_only_while_the_model_receives_images() {
    let vision = with_tools(
        config(),
        json!({"file_system": {
            "mode": "enabled",
            "read_file": {"documents": "extracted_text", "images": "native"},
        }}),
    );
    let mut runtime = SynchronousRuntime::new(vision.clone());
    let first = runtime.start_turn("vision", "read the scan");
    assert_eq!(
        read_file_tool_description(&first),
        format!(
            "{READ_FILE_TEXT_DESCRIPTION} PDF, Word, PowerPoint, and email files return their extracted text. An image file returns the image itself."
        )
    );
    runtime.finish_turn_with_text("vision", &first, "done");

    let mut text_only = vision;
    text_only.settings.context.model_facts.input_modalities = vec![InputModality::Text];
    runtime.reconfigure_settings(
        text_only.settings,
        completed("vision", vec![json!({"type": "text", "text": "done"})]),
    );
    let next = runtime
        .apply(
            user_message("text-only", "read it again", "queue"),
            running("text-only")
                .dispatch(llm_call(0))
                .observe(turn_started("text-only", "read it again")),
        )
        .only_action();

    assert_eq!(next["model_input"]["tool_catalog"]["type"], "replace");
    assert_eq!(
        read_file_tool_description(&next),
        format!(
            "{READ_FILE_TEXT_DESCRIPTION} PDF, Word, PowerPoint, and email files return their extracted text."
        )
    );
}

/// *Prepare*: A Session enables skills, once with no skill configured and once with one.
/// *Do*: Start a Turn in each.
/// *Assert*: Without a skill, no `skill` tool or skill guidance reaches the model, so it is
/// never told to load skills it does not have; with one, both do.
#[test]
fn enabled_skills_advertise_the_skill_tool_only_with_a_skill() {
    let configuration = with_tools(config(), json!({"skills": {"mode": "enabled"}}));
    let mut with_skill = serde_json::to_value(&configuration).unwrap();
    with_skill["capabilities"]["skills"] = json!([{
        "name": "review",
        "description": "Review code",
        "path": "/skills/review/SKILL.md",
    }]);

    let without = SynchronousRuntime::new(configuration).start_turn("no-skills", "work");
    let with = SynchronousRuntime::new(serde_json::from_value(with_skill).unwrap())
        .start_turn("one-skill", "work");

    assert!(!catalog_names(&without).contains(&"skill".to_string()));
    let system = system_text(&without);
    assert!(!system.contains("skill"), "system prompt mentions skills");
    assert!(system.contains(
        "Act directly and proactively. Discover additional tools with `search_tool_functions`"
    ));
    assert!(catalog_names(&with).contains(&"skill".to_string()));
    assert!(system_text(&with).contains("## Available Skills"));
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

fn read_file_tool_description(completion: &Value) -> &str {
    completion["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "read_file")
        .and_then(|tool| tool["description"].as_str())
        .expect("the model tool catalog carries read_file")
}

fn system_text(completion: &Value) -> String {
    completion["model_input"]["messages"]["messages"][0]["content"][0]["text"]
        .as_str()
        .expect("the first model message is the generated system prompt")
        .to_string()
}

const RETIRED_EDIT_GUIDANCE: &str = "file_system.search_replace is deprecated and cannot execute. Use edit, or tools.file_system.edit in run_typescript.";

fn frozen_edit_fixture(name: &str) -> String {
    std::fs::read_to_string(format!(
        "{}/tests/fixtures/checkpoints/retired-v1-search-replace/{name}",
        env!("CARGO_MANIFEST_DIR")
    ))
    .expect("frozen edit fixture exists")
}

fn frozen_edit_action(key: &str) -> Value {
    let actions: Value =
        serde_json::from_str(&frozen_edit_fixture("search-replace-actions.json")).unwrap();
    actions[key].clone()
}

fn retired_edit_failure(action: &Value) -> Value {
    json!({
        "type": "tool_failed",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": {
            "type": "failure",
            "content": [{"type": "text", "text": RETIRED_EDIT_GUIDANCE}],
            "error": {
                "code": "deprecated_tool",
                "message": RETIRED_EDIT_GUIDANCE,
                "retryable": false,
                "details": null,
            },
        },
    })
}

fn edit_ask_config() -> HarnessConfig {
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.*"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    serde_json::from_value(config_value).expect("permission policy is valid")
}

/// *Prepare*: A checkpoint saved before the edit tool took the model's argument names, with a direct edit already issued as `file_system.search_replace`.
/// *Do*: Restore it with current configuration and deliver the Runtime's saved result.
/// *Assert*: The issued Action keeps its old name and arguments, its result settles, and the model is called.
#[test]
fn frozen_retired_search_replace_direct_call_settles_with_its_saved_result() {
    // Prepare
    let fixture = frozen_edit_fixture("active-pending-direct-edit.json");
    let action = frozen_edit_action("direct_pending");
    let mut runtime = SynchronousRuntime::from_checkpoint(config(), &fixture, vec![action.clone()]);
    assert_eq!(
        runtime.checkpoint_value(),
        serde_json::from_str::<Value>(&fixture).unwrap()
    );
    runtime.restart_from_checkpoint();

    // Do
    let response = structured_tool_success(
        &action,
        json!({"file": "notes.txt", "lines_changed": 1, "warnings": []}),
    );
    let next = runtime
        .apply(
            response.clone(),
            running("old-edit")
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&action, &response),
        )
        .only_action();

    // Assert
    runtime.finish_turn_with_text("old-edit", &next, "done");
}

/// *Prepare*: The same checkpoint, restored by a Runtime that has no saved result for the retired Action.
/// *Do*: Deliver the Runtime's `deprecated_tool` failure.
/// *Assert*: The model sees the failure for its `edit` call and can call the tool again.
#[test]
fn frozen_retired_search_replace_direct_call_fails_without_running() {
    // Prepare
    let fixture = frozen_edit_fixture("active-pending-direct-edit.json");
    let action = frozen_edit_action("direct_pending");
    let mut runtime = SynchronousRuntime::from_checkpoint(config(), &fixture, vec![action.clone()]);

    // Do
    let response = retired_edit_failure(&action);
    let next = runtime
        .apply(
            response.clone(),
            running("old-edit")
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&action, &response),
        )
        .only_action();

    // Assert
    let messages = next["model_input"]["messages"]["messages"]
        .as_array()
        .expect("restored request replaces the model messages");
    let tool_message = messages.last().expect("the request has messages");
    assert_eq!(tool_message["role"], "tool");
    assert_eq!(tool_message["tool_call_id"], "call-edit");
    assert_eq!(tool_message["name"], "edit");
    assert_eq!(tool_message["outcome"], "failure");
    assert_eq!(
        tool_message["content"],
        json!([{"type": "text", "text": RETIRED_EDIT_GUIDANCE}])
    );
    runtime.finish_turn_with_text("old-edit", &next, "done");
}

/// *Prepare*: A checkpoint saved with a direct edit awaiting approval under the retired name and its grant key.
/// *Do*: Restore it, approve the saved approval, fail the retired call, and let the model edit again.
/// *Assert*: The approval keeps its old grant key; the new edit asks again under `file_system.edit` with a different key, so a grant saved for the old call never approves it.
#[test]
fn frozen_retired_search_replace_approval_keeps_its_key_and_the_new_edit_asks_again() {
    // Prepare
    let fixture = frozen_edit_fixture("active-direct-edit-awaiting-approval.json");
    let approval = frozen_edit_action("direct_approval");
    let mut runtime =
        SynchronousRuntime::from_checkpoint(edit_ask_config(), &fixture, vec![approval.clone()]);
    assert_eq!(
        runtime.checkpoint_value(),
        serde_json::from_str::<Value>(&fixture).unwrap()
    );

    // Do
    let retired = runtime
        .apply(
            json!({
                "type": "approval_completed",
                "action_id": approval["action_id"],
                "outcome": "approve",
            }),
            running("old-edit").dispatch(frozen_edit_action("direct_pending")),
        )
        .only_action();
    let response = retired_edit_failure(&retired);
    let retry = runtime
        .apply(
            response.clone(),
            running("old-edit")
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&retired, &response),
        )
        .only_action();
    let edit_arguments =
        json!({"file_path": "notes.txt", "old_string": "draft", "new_string": "final"});
    let calls = vec![tool_call("call-edit-again", "edit", edit_arguments.clone())];
    let new_approval = runtime
        .complete_with_tool_calls(
            "old-edit",
            &retry,
            &calls,
            [json!({
                "type": "approval",
                "call_id": "call-edit-again",
                "tool_id": "file_system.edit",
                "display_name": "edit",
                "input": edit_arguments,
            })],
        )
        .only_action();

    // Assert
    assert_eq!(approval["tool_id"], "file_system.search_replace");
    assert_ne!(new_approval["grant_key"], approval["grant_key"]);
}

/// *Prepare*: A checkpoint saved with a `run_typescript` program waiting on `tools.file_system.edit`, issued as `file_system.search_replace`.
/// *Do*: Restore it and deliver the Runtime's saved result.
/// *Assert*: The program receives the result and finishes, and the model is called.
#[test]
fn frozen_retired_search_replace_program_call_settles_with_its_saved_result() {
    // Prepare
    let fixture = frozen_edit_fixture("active-pending-program-edit.json");
    let action = frozen_edit_action("program_pending");
    let mut runtime = SynchronousRuntime::from_checkpoint(config(), &fixture, vec![action.clone()]);
    assert_eq!(
        runtime.checkpoint_value(),
        serde_json::from_str::<Value>(&fixture).unwrap()
    );

    // Do
    let response = structured_tool_success(
        &action,
        json!({"file": "notes.txt", "lines_changed": 1, "warnings": []}),
    );
    let next = runtime
        .apply(
            response.clone(),
            running("old-edit")
                .dispatch(llm_call(1))
                .observe_tool_result(&action, &response)
                .observe_tool_execution_finished("call-program", run_typescript_success(json!(1))),
        )
        .only_action();

    // Assert
    runtime.finish_turn_with_text("old-edit", &next, "done");
}
