use super::*;

fn config_with_skills() -> HarnessConfig {
    let mut config = config();
    config.capabilities.skills = serde_json::from_value(json!([{
        "name": "zeta-review",
        "description": "Review <code> safely.",
        "path": "/skills/zeta<&/SKILL.md",
    }]))
    .expect("root skill is structurally valid");
    config.plugins = serde_json::from_value(json!([{
        "name": "research-plugin",
        "description": "Research capabilities.",
        "path": "/plugins/research",
        "capabilities": {
            "skills": [{
                "name": "alpha-research",
                "description": "Collect evidence.",
                "path": "/plugins/research/skills/alpha/SKILL.md",
            }]
        }
    }]))
    .expect("plugin skill is structurally valid");
    config
}

fn tool_call_completion(
    action_id: &str,
    call_id: &str,
    tool_name: &str,
    arguments: Value,
) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id,
        "result": {
            "parts": [{
                "type": "tool_call",
                "id": call_id,
                "name": tool_name,
                "arguments_json": arguments.to_string(),
            }],
            "finish_reason": "tool_call",
            "usage": null,
        },
    })
}

fn reconfigure(changes: Value) -> Value {
    json!({
        "type": "reconfigure",
        "changes": changes,
    })
}

fn appended_tool_result_text<'a>(result: &'a Value, call_id: &str) -> &'a str {
    result["transition"]["next"]["directives"]
        .as_array()
        .expect("next directives are an array")
        .iter()
        .filter_map(|directive| {
            directive["action"]["model_input"]["messages"]["messages"].as_array()
        })
        .flatten()
        .find(|message| message["role"] == "tool" && message["tool_call_id"] == call_id)
        .and_then(|message| message["content"].as_array())
        .and_then(|content| content.iter().find(|block| block["type"] == "text"))
        .and_then(|block| block["text"].as_str())
        .expect("transition appends the requested tool result")
}

fn skill_succeeded(action: &Value, instructions: &str) -> Value {
    json!({
        "type": "tool_succeeded",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": {
            "type": "success",
            "content": [{"type": "text", "text": instructions}],
            "structured_content": instructions,
        },
    })
}

///
/// *Prepare*: Root and plugin skills are configured in a session that has not synchronized model context or tools.
/// *Do*: Start a turn, call the top-level `skill` tool, checkpoint the pending Runtime action, restore it, and return loaded instructions.
/// *Assert*: Prompt entries preserve source order and escaping, the direct schema stays generic, the route is `skill.read`, and both sessions resume identically.
///
#[test]
fn direct_skill_route_preserves_prompt_catalog_and_restore() {
    // Prepare
    let config = config_with_skills();
    let mut session = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-direct-skill", "load the research skill", "queue"),
    )));
    let completion_action = &started["transition"]["next"]["directives"][0]["action"];
    let completion_action_id = completion_action["action_id"]
        .as_str()
        .expect("turn dispatches a completion")
        .to_string();
    let messages = serde_json::to_string(&completion_action["model_input"]["messages"]["messages"])
        .expect("initial messages serialize");
    let tools = completion_action["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .expect("initial tool catalog is an array");
    let skill_tool = tools
        .iter()
        .find(|tool| tool["name"] == "skill")
        .expect("configured skills expose the direct skill tool");

    // Do
    let pending = accepted_value(session.apply(input(
        2,
        tool_call_completion(
            &completion_action_id,
            "call-direct-skill",
            "skill",
            json!({"name": "alpha-research"}),
        ),
    )));
    let action = &pending["transition"]["next"]["directives"][0]["action"];
    let checkpoint = session.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");
    let command = skill_succeeded(action, "Loaded research instructions.");
    let live = accepted_value(session.apply(input(3, command.clone())));
    let restored_result = accepted_value(restored.apply(input(1, command)));

    // Assert
    assert!(messages.contains("## Available Skills"));
    assert!(messages.contains("Call `skill` only with a name listed in `<available-skills>`"));
    assert!(messages.contains("instructions are already present in the current context"));
    assert!(messages.contains("without calling `skill` again"));
    assert!(messages.contains("Connector names and unlisted names"));
    assert!(messages.contains("Review &lt;code&gt; safely."));
    assert!(messages.contains("/skills/zeta&lt;&amp;/SKILL.md"));
    let zeta_index = messages
        .find("zeta-review")
        .expect("root skill is rendered");
    let alpha_index = messages
        .find("alpha-research")
        .expect("plugin skill is rendered");
    assert!(zeta_index < alpha_index);
    assert!(
        skill_tool["parameters"]["properties"]["name"]
            .get("enum")
            .is_none()
    );
    assert_eq!(
        skill_tool["parameters"]["properties"]["name"]["type"],
        "string"
    );
    assert!(
        skill_tool["description"]
            .as_str()
            .expect("skill description is text")
            .contains("Call exactly with {\"name\":\"<exact name>\"}")
    );
    assert_eq!(action["type"], "runtime_builtin_tool_call");
    assert_eq!(action["call"]["name"], "skill.read");
    assert_eq!(
        action["call"]["arguments"],
        json!({"name": "alpha-research"})
    );
    assert_eq!(
        inspection_value(&restored)["pending_actions"],
        inspection_value(&session)["pending_actions"]
    );
    assert_eq!(
        live["transition"]["observations"],
        restored_result["transition"]["observations"]
    );
    let next_messages = serde_json::to_string(
        &live["transition"]["next"]["directives"][0]["action"]["model_input"]["messages"],
    )
    .expect("next model update serializes");
    assert!(next_messages.contains("Loaded research instructions."));
    assert_eq!(checkpoint_value(&restored), checkpoint_value(&session));
}

#[test]
fn direct_skill_schema_is_identical_for_different_non_empty_catalogs() {
    fn skill_tool_for(name: &str, description: &str) -> Value {
        let mut config = config();
        config.capabilities.skills = serde_json::from_value(json!([{
            "name": name,
            "description": description,
            "path": format!("/skills/{name}/SKILL.md"),
        }]))
        .expect("skill catalog is valid");
        let mut session = HarnessSession::create(config).expect("session creates");
        let started = accepted_value(
            session.apply(input(1, user_message("turn-skill-schema", "work", "queue"))),
        );
        started["transition"]["next"]["directives"][0]["action"]["model_input"]
            ["tool_catalog"]["tools"]
            .as_array()
            .expect("initial tool catalog is an array")
            .iter()
            .find(|tool| tool["name"] == "skill")
            .expect("configured skills expose the direct skill tool")
            .clone()
    }

    assert_eq!(
        skill_tool_for("review", "Review code."),
        skill_tool_for("research", "Research evidence.")
    );
}

///
/// *Prepare*: The frozen checkpoint V1 fixture contains one pending direct `skill.read` action.
/// *Do*: Decode and restore that fixture, then return the skill instructions through the serialized Step Protocol.
/// *Assert*: The V1 route reconstructs the pending action, accepts its result, and resumes model completion.
///
#[test]
fn frozen_skill_checkpoint_restores_and_resumes() {
    // Prepare
    let fixture = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/checkpoints/v1/active-skill-read.json"
    ));
    let decoded = decode_checkpoint(fixture).expect("frozen V1 checkpoint decodes");
    let mut session = HarnessSession::restore(config_with_skills(), decoded, 0)
        .expect("frozen V1 checkpoint restores");
    let before = inspection_value(&session);
    let skill_action = before["pending_actions"]
        .as_array()
        .expect("pending actions are an array")
        .iter()
        .find(|action| action["name"] == "skill.read")
        .expect("fixture contains a pending skill action");
    let skill_call_id = skill_action["call_id"]
        .as_str()
        .expect("pending skill action has a call ID")
        .to_string();
    assert_eq!(
        before["pending_actions"]
            .as_array()
            .expect("pending actions are an array")
            .len(),
        1
    );

    // Do
    let resumed = accepted_value(session.apply(input(
        1,
        skill_succeeded(skill_action, "Loaded frozen skill instructions."),
    )));
    let after = inspection_value(&session);

    // Assert
    assert_eq!(skill_action["type"], "runtime_builtin_tool_call");
    assert_eq!(resumed["transition"]["turn"]["status"], "running");
    assert_eq!(
        resumed["transition"]["next"]["directives"][0]["action"]["type"],
        "llm_call"
    );
    let resumed_messages = serde_json::to_string(
        &resumed["transition"]["next"]["directives"][0]["action"]["model_input"]["messages"],
    )
    .expect("resumed model messages serialize");
    assert!(resumed_messages.contains("Loaded frozen skill instructions."));
    assert_eq!(after["pending_actions"][0]["type"], "completion");
    assert!(
        after["pending_actions"]
            .as_array()
            .expect("pending actions are an array")
            .iter()
            .all(|action| action["call_id"] != skill_call_id)
    );
}

///
/// *Prepare*: A configured skill appears in the direct tool catalog but is absent from programmatic discovery.
/// *Do*: Request exact `skill.read` details through `search_tool_functions`.
/// *Assert*: Core reports that no programmatic function exists, emits no Runtime action, and asks the model to continue.
///
#[test]
fn skill_loading_is_direct_only() {
    // Prepare
    let mut session = HarnessSession::create(config_with_skills()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-programmatic-skill", "load review guidance", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let direct_tools = started["transition"]["next"]["directives"][0]["action"]["model_input"]
        ["tool_catalog"]["tools"]
        .as_array()
        .expect("initial tool catalog is an array");

    // Do
    let result = accepted_value(session.apply(input(
        2,
        tool_call_completion(
            &completion_action_id,
            "call-search-skill",
            "search_tool_functions",
            json!({"mode": "details", "functions": ["skill.read"]}),
        ),
    )));

    // Assert
    assert!(direct_tools.iter().any(|tool| tool["name"] == "skill"));
    let directives = result["transition"]["next"]["directives"]
        .as_array()
        .expect("next directives are an array");
    assert_eq!(
        directives[0]["action"]["type"], "llm_call",
        "a local catalog lookup returns control to the model"
    );
    assert!(
        directives
            .iter()
            .all(|directive| directive["action"]["type"] != "runtime_builtin_tool_call")
    );
    assert!(
        appended_tool_result_text(&result, "call-search-skill")
            .contains("No exact tool function found for: skill.read")
    );
    assert_eq!(
        inspection_value(&session)["pending_actions"][0]["type"],
        "completion"
    );
}

///
/// *Prepare*: A completed session has synchronized model messages and tools with no configured skills.
/// *Do*: Add one skill, reject an invalid duplicate update, then remove all skills through serialized reconfiguration commands.
/// *Assert*: Skill changes replace both Runtime caches, and invalid updates are atomic and non-consuming.
///
#[test]
fn skill_reconfiguration_updates_caches_and_rejects_atomically() {
    // Prepare
    let base_config = config();
    let mut session = HarnessSession::create(base_config.clone()).expect("session creates");
    let first_started = accepted_value(session.apply(input(
        1,
        user_message("turn-before-skills", "start", "queue"),
    )));
    let first_action_id = dispatched_action_id(&first_started).to_string();
    accepted_value(session.apply(input(
        2,
        completion(
            &first_action_id,
            json!([{"type": "text", "text": "first done"}]),
        ),
    )));
    let mut with_skill =
        serde_json::to_value(&base_config.capabilities).expect("capabilities serialize");
    with_skill["skills"] = json!([{
        "name": "review",
        "description": "Review the current implementation.",
        "path": "/skills/review/SKILL.md",
    }]);
    let mut invalid = with_skill.clone();
    invalid["skills"] = json!([
        {
            "name": "review",
            "description": "Review the current implementation.",
            "path": "/skills/review/SKILL.md",
        },
        {
            "name": "review",
            "description": "Duplicate review instructions.",
            "path": "/skills/review-duplicate/SKILL.md",
        }
    ]);

    // Do
    accepted_value(session.apply(input(
        3,
        reconfigure(json!([{"type": "capabilities", "value": with_skill}])),
    )));
    let with_skill_started = accepted_value(session.apply(input(
        4,
        user_message("turn-with-skills", "review", "queue"),
    )));
    let with_skill_action = &with_skill_started["transition"]["next"]["directives"][0]["action"];
    let second_action_id = with_skill_action["action_id"]
        .as_str()
        .expect("turn dispatches a completion")
        .to_string();
    accepted_value(session.apply(input(
        5,
        completion(
            &second_action_id,
            json!([{"type": "text", "text": "review done"}]),
        ),
    )));
    let checkpoint_before_invalid = checkpoint_value(&session);
    let rejected = serde_json::to_value(session.apply(input(
        6,
        reconfigure(json!([{"type": "capabilities", "value": invalid}])),
    )))
    .expect("reconfiguration result serializes");
    let checkpoint_after_rejected = checkpoint_value(&session);
    let inspection_after_rejected = inspection_value(&session);
    let after_rejection_started = accepted_value(session.apply(input(
        6,
        user_message("turn-after-rejected-skills", "load review again", "queue"),
    )));
    let after_rejection_completion_id = dispatched_action_id(&after_rejection_started).to_string();
    let after_rejection_pending = accepted_value(session.apply(input(
        7,
        tool_call_completion(
            &after_rejection_completion_id,
            "call-review-after-rejection",
            "skill",
            json!({"name": "review"}),
        ),
    )));
    let unchanged_skill_action =
        after_rejection_pending["transition"]["next"]["directives"][0]["action"].clone();
    let after_rejection_resumed = accepted_value(session.apply(input(
        8,
        skill_succeeded(&unchanged_skill_action, "Review skill remains configured."),
    )));
    let after_rejection_resumed_id = dispatched_action_id(&after_rejection_resumed).to_string();
    accepted_value(session.apply(input(
        9,
        completion(
            &after_rejection_resumed_id,
            json!([{"type": "text", "text": "review still available"}]),
        ),
    )));
    accepted_value(session.apply(input(
        10,
        reconfigure(json!([{
            "type": "capabilities",
            "value": base_config.capabilities,
        }])),
    )));
    let without_skill_started = accepted_value(session.apply(input(
        11,
        user_message("turn-without-skills", "continue", "queue"),
    )));
    let without_skill_action =
        &without_skill_started["transition"]["next"]["directives"][0]["action"];

    // Assert
    assert_eq!(
        with_skill_action["model_input"]["messages"]["type"],
        "replace"
    );
    assert_eq!(
        with_skill_action["model_input"]["tool_catalog"]["type"],
        "replace"
    );
    let with_skill_messages =
        serde_json::to_string(&with_skill_action["model_input"]["messages"]["messages"])
            .expect("messages serialize");
    assert!(with_skill_messages.contains("## Available Skills"));
    assert!(with_skill_messages.contains("Review the current implementation."));
    assert!(
        with_skill_action["model_input"]["tool_catalog"]["tools"]
            .as_array()
            .expect("tool catalog is an array")
            .iter()
            .any(|tool| tool["name"] == "skill")
    );
    let with_skill_tool = with_skill_action["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .expect("tool catalog is an array")
        .iter()
        .find(|tool| tool["name"] == "skill")
        .expect("reconfiguration exposes the skill tool");
    assert!(
        with_skill_tool["parameters"]["properties"]["name"]
            .get("enum")
            .is_none()
    );

    assert_eq!(rejected["type"], "rejected");
    assert_eq!(
        rejected["rejection"]["error"]["message"],
        "duplicate skill name \"review\""
    );
    assert_eq!(checkpoint_after_rejected, checkpoint_before_invalid);
    assert_eq!(inspection_after_rejected["last_input_id"], 5);
    assert_eq!(unchanged_skill_action["type"], "runtime_builtin_tool_call");
    assert_eq!(unchanged_skill_action["call"]["name"], "skill.read");
    assert_eq!(
        unchanged_skill_action["call"]["arguments"],
        json!({"name": "review"})
    );

    assert_eq!(
        without_skill_action["model_input"]["messages"]["type"],
        "replace"
    );
    assert_eq!(
        without_skill_action["model_input"]["tool_catalog"]["type"],
        "replace"
    );
    let without_skill_messages =
        serde_json::to_string(&without_skill_action["model_input"]["messages"]["messages"])
            .expect("messages serialize");
    assert!(!without_skill_messages.contains("## Available Skills"));
    assert!(
        without_skill_action["model_input"]["tool_catalog"]["tools"]
            .as_array()
            .expect("tool catalog is an array")
            .iter()
            .all(|tool| tool["name"] != "skill")
    );
}

///
/// *Prepare*: Complete configurations contain an invalid name, blank metadata, a relative or non-SKILL path, duplicate names, or duplicate paths across root and plugin skills.
/// *Do*: Create a `HarnessSession` from each configuration.
/// *Assert*: Creation rejects every configuration with the existing skill validation message.
///
#[test]
fn invalid_skill_definitions_are_rejected_at_session_creation() {
    // Prepare
    let config_with = |root_skills: Value, plugin_skills: Value| {
        let mut value = serde_json::to_value(config()).expect("base config serializes");
        value["capabilities"]["skills"] = root_skills;
        value["plugins"] = json!([{
            "name": "plugin",
            "description": "Plugin capabilities.",
            "path": "/plugins/plugin",
            "capabilities": {"skills": plugin_skills},
        }]);
        serde_json::from_value::<HarnessConfig>(value).expect("config is structurally valid")
    };
    let valid = |name: &str, path: &str| {
        json!({
            "name": name,
            "description": "Skill instructions.",
            "path": path,
        })
    };
    let cases = [
        (
            config_with(
                json!([valid("bad name", "/skills/bad/SKILL.md")]),
                json!([]),
            ),
            "skill name \"bad name\" must be an Agent Skill name or a Runtime-qualified namespace:name alias",
        ),
        (
            config_with(
                json!([{
                    "name": "blank",
                    "description": " ",
                    "path": "/skills/blank/SKILL.md",
                }]),
                json!([]),
            ),
            "skill descriptions and paths must not be empty",
        ),
        (
            config_with(
                json!([valid("wrong-path", "/skills/wrong/README.md")]),
                json!([]),
            ),
            "skill path \"/skills/wrong/README.md\" must point to SKILL.md",
        ),
        (
            config_with(
                json!([valid("relative-path", "skills/review/SKILL.md")]),
                json!([]),
            ),
            "skill path \"skills/review/SKILL.md\" must point to SKILL.md",
        ),
        (
            config_with(
                json!([valid("duplicate", "/skills/root/SKILL.md")]),
                json!([valid("duplicate", "/skills/plugin/SKILL.md")]),
            ),
            "duplicate skill name \"duplicate\"",
        ),
        (
            config_with(
                json!([valid("root-skill", "/skills/shared/SKILL.md")]),
                json!([valid("plugin-skill", "/skills/shared/SKILL.md")]),
            ),
            "duplicate skill path \"/skills/shared/SKILL.md\"",
        ),
    ];

    // Do
    let errors = cases.map(|(config, expected)| {
        let error = HarnessSession::create(config)
            .err()
            .expect("invalid skills reject session creation");
        (error.to_string(), expected)
    });

    // Assert
    for (actual, expected) in errors {
        assert_eq!(actual, expected);
    }
}

///
/// *Prepare*: A session exposes two known skills, but the model calls the direct `skill` tool with a nearby unknown name.
/// *Do*: Apply the model completion containing that tool call.
/// *Assert*: Core emits no Runtime action and returns a recoverable failed tool result with exact-name suggestions and the available skills.
///
#[test]
fn unknown_direct_skill_is_a_recoverable_tool_result() {
    // Prepare
    let mut harness_config = config();
    harness_config.capabilities.skills = serde_json::from_value(json!([
        {
            "name": "rde-veille",
            "description": "Run the complete watch workflow.",
            "path": "/skills/rde-veille/SKILL.md",
        },
        {
            "name": "veille-presse-cdc",
            "description": "Monitor CDC press coverage.",
            "path": "/skills/veille-presse-cdc/SKILL.md",
        },
    ]))
    .expect("production-like skills are structurally valid");
    let mut session = HarnessSession::create(harness_config).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-unknown-skill", "load a missing skill", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();

    // Do
    let result = accepted_value(session.apply(input(
        2,
        tool_call_completion(
            &completion_action_id,
            "call-unknown-skill",
            "skill",
            json!({"name": "rde-veille-collecte"}),
        ),
    )));

    // Assert
    assert_eq!(
        result["transition"]["next"]["directives"][0]["action"]["type"],
        "llm_call"
    );
    assert!(
        result["transition"]["next"]["directives"]
            .as_array()
            .expect("next directives are an array")
            .iter()
            .all(|directive| directive["action"]["type"] != "runtime_builtin_tool_call")
    );
    let failure_text = appended_tool_result_text(&result, "call-unknown-skill");
    assert!(failure_text.contains(r#"Skill \"rde-veille-collecte\" is not available"#));
    assert!(failure_text.contains(r#"Did you mean: \"rde-veille\""#));
    assert!(failure_text.contains("Use an exact name from the available skills below"));
    assert!(failure_text.contains("Connector names and unlisted names in skill instructions"));
    assert!(failure_text.contains("rde-veille"));
    assert!(failure_text.contains("veille-presse-cdc"));
}

///
/// *Prepare*: A session exposes a valid skill, but the model adds a separate parameters argument.
/// *Do*: Apply the malformed direct `skill` call.
/// *Assert*: Core rejects the wrong key before Runtime dispatch and returns schema feedback to the model.
///
#[test]
fn wrong_skill_argument_key_is_a_recoverable_tool_result() {
    // Prepare
    let mut session = HarnessSession::create(config_with_skills()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-wrong-skill-key", "load research guidance", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();

    // Do
    let result = accepted_value(session.apply(input(
        2,
        tool_call_completion(
            &completion_action_id,
            "call-wrong-skill-key",
            "skill",
            json!({
                "name": "alpha-research",
                "parameters": {"topic": "example"},
            }),
        ),
    )));

    // Assert
    assert!(
        result["transition"]["next"]["directives"]
            .as_array()
            .expect("next directives are an array")
            .iter()
            .all(|directive| directive["action"]["type"] != "runtime_builtin_tool_call")
    );
    let failure_text = appended_tool_result_text(&result, "call-wrong-skill-key");
    assert!(failure_text.contains("tool arguments do not satisfy the declared input schema"));
    assert!(failure_text.contains("parameters"));
}

///
/// *Prepare*: A pending completion is checkpointed with one skill catalog, then the Host restores it with a replacement catalog.
/// *Do*: Request a model-input refresh and call one name from the old catalog.
/// *Assert*: The restored prompt and validation use only the current skill configuration while the direct schema stays generic.
///
#[test]
fn restore_uses_the_current_skill_prompt_catalog_and_validation() {
    // Prepare
    let old_config = config_with_skills();
    let mut session = HarnessSession::create(old_config).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-restored-skills", "load current guidance", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let checkpoint = session.checkpoint().expect("checkpoint captures");
    let mut current_config = config();
    current_config.capabilities.skills = serde_json::from_value(json!([{
        "name": "current-review",
        "description": "Review with the current process.",
        "path": "/skills/current-review/SKILL.md",
    }]))
    .expect("current skills are structurally valid");
    let mut restored =
        HarnessSession::restore(current_config, checkpoint, 0).expect("checkpoint restores");

    // Do
    let refreshed = accepted_value(restored.apply(input(
        1,
        json!({
            "type": "completion_model_input_resync_requested",
            "action_id": completion_action_id,
        }),
    )));
    let refreshed_action = &refreshed["transition"]["next"]["directives"][0]["action"];
    let invalid = accepted_value(restored.apply(input(
        2,
        tool_call_completion(
            &completion_action_id,
            "call-stale-skill",
            "skill",
            json!({"name": "alpha-research"}),
        ),
    )));

    // Assert
    let refreshed_messages =
        serde_json::to_string(&refreshed_action["model_input"]["messages"]["messages"])
            .expect("refreshed messages serialize");
    assert!(refreshed_messages.contains("current-review"));
    assert!(!refreshed_messages.contains("zeta-review"));
    assert!(!refreshed_messages.contains("alpha-research"));
    let refreshed_skill_tool = refreshed_action["model_input"]["tool_catalog"]["tools"]
        .as_array()
        .expect("refreshed tool catalog is an array")
        .iter()
        .find(|tool| tool["name"] == "skill")
        .expect("current skills expose the skill tool");
    assert!(
        refreshed_skill_tool["parameters"]["properties"]["name"]
            .get("enum")
            .is_none()
    );
    assert!(
        invalid["transition"]["next"]["directives"]
            .as_array()
            .expect("next directives are an array")
            .iter()
            .all(|directive| directive["action"]["type"] != "runtime_builtin_tool_call")
    );
    let failure_text = appended_tool_result_text(&invalid, "call-stale-skill");
    assert!(failure_text.contains(r#"Skill \"alpha-research\" is not available"#));
    assert!(failure_text.contains(r#"Available skills: \"current-review\"."#));
}
