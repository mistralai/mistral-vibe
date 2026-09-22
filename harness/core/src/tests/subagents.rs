use super::*;

struct SubagentCase {
    runtime_name: &'static str,
    arguments: Value,
    result: Value,
}

fn subagent_cases() -> Vec<SubagentCase> {
    vec![
        SubagentCase {
            runtime_name: "subagent.list",
            arguments: json!({}),
            result: json!({
                "agents": [
                    {
                        "agentName": "running-agent",
                        "agentType": "reviewer",
                        "status": "running",
                        "pendingTurn": true,
                    },
                    {
                        "agentName": "idle-agent",
                        "agentType": null,
                        "status": "idle",
                        "pendingTurn": false,
                    },
                    {
                        "agentName": "stopped-agent",
                        "status": "stopped",
                        "pendingTurn": false,
                    },
                    {
                        "agentName": "failed-agent",
                        "status": "failed",
                        "pendingTurn": false,
                        "error": "failed",
                    },
                ]
            }),
        },
        SubagentCase {
            runtime_name: "subagent.spawn",
            arguments: json!({
                "agentName": "researcher",
                "message": "Investigate the issue",
                "agentType": "reviewer",
            }),
            result: json!({"type": "success"}),
        },
        SubagentCase {
            runtime_name: "subagent.wait",
            arguments: json!({"agentName": "researcher", "timeoutMs": 1_000}),
            result: json!({"type": "success", "value": "final answer"}),
        },
        SubagentCase {
            runtime_name: "subagent.send_message",
            arguments: json!({
                "agentName": "researcher",
                "message": "Check the latest evidence",
            }),
            result: json!({"type": "success"}),
        },
        SubagentCase {
            runtime_name: "subagent.interrupt",
            arguments: json!({"agentName": "researcher"}),
            result: json!({"type": "success"}),
        },
        SubagentCase {
            runtime_name: "subagent.stop",
            arguments: json!({"agentName": "researcher"}),
            result: json!({"type": "success"}),
        },
    ]
}

fn run_typescript_completion(action_id: &str, call_id: &str, source: &str) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id,
        "result": {
            "parts": [{
                "type": "tool_call",
                "id": call_id,
                "name": "run_typescript",
                "arguments_json": json!({"code": source}).to_string(),
            }],
            "finish_reason": "tool_call",
            "usage": null,
        },
    })
}

fn structured_tool_success(action: &Value, structured_content: &Value) -> Value {
    json!({
        "type": "tool_succeeded",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": {
            "type": "success",
            "content": [],
            "structured_content": structured_content,
        },
    })
}

fn config_with_agent_types() -> HarnessConfig {
    let mut config = config();
    config.capabilities.agent_types = serde_json::from_value(json!([
        {
            "name": "zeta-reviewer",
            "description": "Reviews <code> changes.",
            "path": "/agents/zeta-reviewer.toml",
        }
    ]))
    .expect("root agent type is valid");
    config.plugins = serde_json::from_value(json!([
        {
            "name": "research-plugin",
            "description": "Research capabilities.",
            "path": "/plugins/research",
            "capabilities": {
                "agent_types": [{
                    "name": "alpha-researcher",
                    "description": "Collects evidence.",
                    "path": "/plugins/research/agents/alpha.toml",
                }]
            }
        }
    ]))
    .expect("plugin agent type is valid");
    config
}

fn serialized_initial_messages(session: &mut HarnessSession, turn_id: &str) -> (Value, String) {
    let started = accepted_value(session.apply(input(
        1,
        user_message(turn_id, "coordinate subagents", "queue"),
    )));
    let messages = started["transition"]["next"]["directives"][0]["action"]["model_input"]
        ["messages"]["messages"]
        .clone();
    let serialized = serde_json::to_string(&messages).expect("initial messages serialize");
    (started, serialized)
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

///
/// *Prepare*: One `run_typescript` call requests every subagent operation in parallel while subagents are enabled.
/// *Do*: Serialize all six Runtime actions, checkpoint the pending program, and restore it into a new session.
/// *Assert*: Programmatic names map to stable qualified Runtime routes, restoration preserves every pending route, and all declared result schemas resume both sessions identically.
///
#[test]
fn programmatic_subagent_route_matrix_survives_restore() {
    // Prepare
    let config = config();
    let mut session = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message(
            "turn-programmatic-subagents",
            "use every subagent operation",
            "queue",
        ),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let source = "async function main() { return Promise.all([tools.agent.list({}), tools.agent.spawn({ agentName: 'researcher', message: 'Investigate the issue', agentType: 'reviewer' }), tools.agent.wait({ agentName: 'researcher', timeoutMs: 1000 }), tools.agent.sendMessage({ agentName: 'researcher', message: 'Check the latest evidence' }), tools.agent.interrupt({ agentName: 'researcher' }), tools.agent.close({ agentName: 'researcher' })]); }";

    // Do
    let pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(&completion_action_id, "call-programmatic-subagents", source),
    )));
    let actions = pending["transition"]["next"]["directives"]
        .as_array()
        .expect("program dispatches subagent actions")
        .iter()
        .map(|directive| directive["action"].clone())
        .collect::<Vec<_>>();
    let checkpoint = session.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored = HarnessSession::restore(config, decoded, 0).expect("checkpoint restores");

    // Assert
    let expected = subagent_cases();
    assert_eq!(actions.len(), expected.len());
    for (action, case) in actions.iter().zip(&expected) {
        assert_eq!(action["type"], "runtime_builtin_tool_call");
        assert_eq!(action["call"]["name"], case.runtime_name);
        assert_eq!(action["call"]["arguments"], case.arguments);
    }
    assert_eq!(
        inspection_value(&restored)["pending_actions"],
        inspection_value(&session)["pending_actions"]
    );
    assert_eq!(checkpoint_value(&restored), checkpoint_value(&session));

    for (index, (action, case)) in actions.iter().zip(&expected).enumerate() {
        let command = structured_tool_success(action, &case.result);
        let live = accepted_value(session.apply(input(3 + index as u64, command.clone())));
        let restored_result = accepted_value(restored.apply(input(1 + index as u64, command)));

        assert_eq!(
            live["transition"]["observations"],
            restored_result["transition"]["observations"]
        );
        assert_eq!(
            live["transition"]["observations"][0]["type"],
            "tool_result_committed"
        );
        assert_eq!(
            live["transition"]["observations"][0]["action_id"],
            action["action_id"]
        );
        assert_eq!(checkpoint_value(&restored), checkpoint_value(&session));

        if index + 1 == expected.len() {
            assert_eq!(
                live["transition"]["next"]["directives"][0]["action"]["type"],
                "llm_call"
            );
            assert_eq!(
                restored_result["transition"]["next"]["directives"][0]["action"]["type"],
                "llm_call"
            );
        }
    }
}

///
/// *Prepare*: The same root and plugin agent types are supplied to sessions using the tagged enabled and disabled subagent settings.
/// *Do*: Start both sessions, then attempt `tools.agent.list` in the disabled session.
/// *Assert*: The settings keep their exact JSON format, enabled model context contains escaped and sorted guidance, and disabled mode hides every subagent route.
///
#[test]
fn disabled_subagents_hide_agent_context_and_runtime_routes() {
    // Prepare
    let enabled_config = config_with_agent_types();
    let enabled_settings =
        serde_json::to_value(&enabled_config.settings).expect("enabled settings serialize");
    let mut enabled =
        HarnessSession::create(enabled_config.clone()).expect("enabled session creates");
    let (_, enabled_messages) = serialized_initial_messages(&mut enabled, "turn-subagents-enabled");
    let mut disabled_config = enabled_config;
    let mut disabled_settings = enabled_settings.clone();
    disabled_settings["tools"]["subagents"] = json!({"mode": "disabled"});
    disabled_config.settings = serde_json::from_value(disabled_settings.clone())
        .expect("disabled settings deserialize from the public wire format");
    let mut reconfigure_session =
        HarnessSession::create(disabled_config.clone()).expect("reconfigure session creates");
    let checkpoint_before_reconfigure = checkpoint_value(&reconfigure_session);
    let reconfigure_rejection = serde_json::to_value(reconfigure_session.apply(input(
        1,
        json!({
            "type": "reconfigure",
            "changes": [{"type": "settings", "value": enabled_settings.clone()}],
        }),
    )))
    .expect("reconfigure rejection serializes");
    let mut disabled = HarnessSession::create(disabled_config).expect("disabled session creates");
    let (disabled_started, disabled_messages) =
        serialized_initial_messages(&mut disabled, "turn-subagents-disabled");
    let completion_action_id = dispatched_action_id(&disabled_started).to_string();

    // Do
    let attempted = accepted_value(disabled.apply(input(
        2,
        run_typescript_completion(
            &completion_action_id,
            "call-disabled-subagents",
            "async function main() { return tools.agent.list({}); }",
        ),
    )));

    // Assert
    assert_eq!(
        enabled_settings["tools"]["subagents"],
        json!({"mode": "enabled"})
    );
    assert_eq!(
        disabled_settings["tools"]["subagents"],
        json!({"mode": "disabled"})
    );
    assert!(enabled_messages.contains("## Subagents"));
    assert!(enabled_messages.contains("name never used by this parent session"));
    assert!(enabled_messages.contains("keeps its name reserved"));
    assert!(enabled_messages.contains("## Available agent types"));
    assert!(enabled_messages.contains("Reviews &lt;code&gt; changes."));
    let alpha_index = enabled_messages
        .find("alpha-researcher")
        .expect("plugin agent type is rendered");
    let zeta_index = enabled_messages
        .find("zeta-reviewer")
        .expect("root agent type is rendered");
    assert!(alpha_index < zeta_index);
    assert!(!disabled_messages.contains("## Subagents"));
    assert!(!disabled_messages.contains("## Available agent types"));
    assert!(!disabled_messages.contains("alpha-researcher"));
    assert!(!disabled_messages.contains("zeta-reviewer"));
    assert_eq!(reconfigure_rejection["type"], "rejected");
    assert_eq!(
        reconfigure_rejection["rejection"]["error"]["message"],
        "settings.tools.subagents cannot be reconfigured after session creation"
    );
    assert_eq!(
        checkpoint_value(&reconfigure_session),
        checkpoint_before_reconfigure
    );
    assert_eq!(inspection_value(&reconfigure_session)["last_input_id"], 0);
    let directives = attempted["transition"]["next"]["directives"]
        .as_array()
        .expect("disabled attempt produces directives");
    assert_eq!(directives.len(), 1);
    assert_eq!(directives[0]["action"]["type"], "llm_call");
}

///
/// *Prepare*: A pre-tool hook selects only the canonical `subagent.spawn` identity.
/// *Do*: Start a programmatic spawn and complete the selected hook with effective arguments.
/// *Assert*: Core emits the matching hook first, then dispatches the qualified subagent Runtime action with the hook-approved arguments.
///
#[test]
fn subagent_tool_hooks_use_the_canonical_runtime_identity() {
    // Prepare
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([
        {
            "id": "subagent-spawn-pre",
            "point": "pre_tool_call",
            "order": 0,
            "selector": {
                "type": "tool_keys",
                "tool_keys": [{
                    "target": "subagent",
                    "qualified_name": "subagent.spawn",
                }]
            }
        }
    ]))
    .expect("subagent hook configuration is valid");
    let mut session = HarnessSession::create(config.clone()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-subagent-hook", "spawn a reviewer", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let hook_pending = accepted_value(session.apply(input(
        2,
        run_typescript_completion(
            &completion_action_id,
            "call-subagent-hook",
            "async function main() { return tools.agent.spawn({ agentName: 'researcher', message: 'Investigate' }); }",
        ),
    )));
    let hook_action = &hook_pending["transition"]["next"]["directives"][0]["action"];
    let hook_action_id = hook_action["action_id"]
        .as_str()
        .expect("hook action has an ID")
        .to_string();
    let checkpoint = session.checkpoint().expect("hook checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("hook checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("hook checkpoint decodes");
    let mut restored =
        HarnessSession::restore(config, decoded, 0).expect("hook checkpoint restores");
    let hook_result = json!({
        "type": "hook_completed",
        "action_id": hook_action_id,
        "result": {
            "hook": "pre_tool_call",
            "output": {
                "type": "continue",
                "effective_arguments": {
                    "agentName": "reviewer",
                    "message": "Inspect the patch",
                    "agentType": "code-reviewer",
                },
            },
        },
    });

    // Do
    let tool_pending = accepted_value(session.apply(input(3, hook_result.clone())));
    let restored_tool_pending = accepted_value(restored.apply(input(1, hook_result)));

    // Assert
    assert_eq!(hook_action["type"], "hook_call");
    assert_eq!(hook_action["hook"], "pre_tool_call");
    assert_eq!(
        hook_action["hook_binding_ids"],
        json!(["subagent-spawn-pre"])
    );
    assert_eq!(
        hook_action["input"]["tool_call"]["call"]["name"],
        "subagent.spawn"
    );
    let tool_action = &tool_pending["transition"]["next"]["directives"][0]["action"];
    let restored_tool_action =
        &restored_tool_pending["transition"]["next"]["directives"][0]["action"];
    assert_eq!(restored_tool_action, tool_action);
    assert_eq!(tool_action["type"], "runtime_builtin_tool_call");
    assert_eq!(tool_action["call"]["name"], "subagent.spawn");
    assert_eq!(
        tool_action["call"]["arguments"],
        json!({
            "agentName": "reviewer",
            "message": "Inspect the patch",
            "agentType": "code-reviewer",
        })
    );
}
