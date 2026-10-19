use super::*;

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
