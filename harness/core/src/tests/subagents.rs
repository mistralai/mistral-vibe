use super::*;

/// *Prepare*: Discovery wording is empty or contains only whitespace.
/// *Do*: Create Core Sessions from each serialized configuration.
/// *Assert*: Blank tool descriptions are rejected before model context is built.
#[test]
fn operator_discovery_description_rejects_blank_text() {
    // Prepare
    for description in ["", " \n\t"] {
        let mut settings = serde_json::to_value(config()).unwrap();
        settings["settings"]["prompts"]["discovery_description"] = json!(description);

        // Do
        let result = HarnessSession::create(serde_json::from_value(settings).unwrap());

        // Assert
        assert!(result.is_err());
    }
}

/// *Prepare*: Task wording contains expressions, missing identity tokens, or escaped token names.
/// *Do*: Create Core Sessions from each serialized configuration.
/// *Assert*: Invalid templates are rejected before any task can enter model context.
#[test]
fn operator_task_templates_reject_unknown_missing_and_escaped_identity_tokens() {
    // Prepare
    for template in [
        "You are {recipient_agent_path.name}; task from {sender_agent_path}",
        "You are {recipient_agent_path}; task from {sender_agent_path:}",
        "Task from {sender_agent_path}",
        "You are {{recipient_agent_path}}; task from {sender_agent_path}",
        "You are {recipient_agent_path}; task from {sender_agent_path",
    ] {
        let mut settings = serde_json::to_value(config()).unwrap();
        settings["settings"]["prompts"]["agent_messages"]["current_task"] = json!(template);

        // Do
        let result = HarnessSession::create(serde_json::from_value(settings).unwrap());

        // Assert
        assert!(result.is_err(), "accepted invalid template: {template}");
    }
}

///
/// *Prepare*: Create configurations with duplicate agent-type names across root and plugin capabilities, a blank description, and 129 definitions.
/// *Do*: Create a `HarnessSession` from each complete configuration.
/// *Assert*: Creation rejects each configuration with the existing agent-type validation message.
///
#[test]
fn invalid_agent_type_definitions_are_rejected_at_session_creation() {
    // Prepare
    let mut duplicate = config();
    duplicate.capabilities.agent_types = serde_json::from_value(json!([{
        "name": "duplicate",
        "description": "Root definition.",
        "path": "/agents/root.toml",
    }]))
    .expect("root agent type is valid");
    duplicate.plugins = serde_json::from_value(json!([{
        "name": "plugin",
        "description": "Plugin capabilities.",
        "path": "/plugins/plugin",
        "capabilities": {
            "agent_types": [{
                "name": "duplicate",
                "description": "Plugin definition.",
                "path": "/plugins/plugin/agents/duplicate.toml",
            }]
        }
    }]))
    .expect("plugin agent type is valid");

    let mut blank_description = config();
    blank_description.capabilities.agent_types = serde_json::from_value(json!([{
        "name": "reviewer",
        "description": " ",
        "path": "/agents/reviewer.toml",
    }]))
    .expect("blank agent type remains structurally valid");

    let mut over_limit = config();
    over_limit.capabilities.agent_types = serde_json::from_value(Value::Array(
        (0..129)
            .map(|index| {
                json!({
                    "name": format!("agent-{index}"),
                    "description": "Agent definition.",
                    "path": format!("/agents/{index}.toml"),
                })
            })
            .collect(),
    ))
    .expect("agent type list is structurally valid");

    let cases = [
        (duplicate, "duplicate agent type name \"duplicate\""),
        (
            blank_description,
            "agent type description must not be empty",
        ),
        (
            over_limit,
            "configuration contains more than 128 agent types",
        ),
    ];

    // Do
    let errors = cases.map(|(config, expected)| {
        let error = HarnessSession::create(config)
            .err()
            .expect("invalid agent types reject session creation");
        (error.to_string(), expected)
    });

    // Assert
    for (actual, expected) in errors {
        assert_eq!(actual, expected);
    }
}
