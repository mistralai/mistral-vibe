use serde_json::Value;
use serde_json::json;

use crate::core::HarnessApplyResult;
use crate::core::HarnessConfig;
use crate::core::HarnessInput;
use crate::core::HarnessSession;

// This is the sole canonical snapshot for the initial model context and tool
// catalog. Read docs/snapshot-testing.md before adding overlapping snapshots.
#[test]
fn initial_llm_request_with_all_capabilities_enabled() {
    let mut session = HarnessSession::create(canonical_config()).unwrap();
    let result = session.apply(initial_user_message());
    let request = initial_llm_call(result);

    insta::assert_json_snapshot!("initial_llm_request", reviewable_snapshot(request));
}

fn canonical_config() -> HarnessConfig {
    serde_json::from_value(json!({
        "task_id": "canonical-context-snapshot",
        "system_instructions": r#"You are operating in the canonical Unified Harness snapshot environment.

- Prefer precise, verifiable work.
- Treat `/workspace` as the project root.
- Use the configured Acme tools and skills when relevant."#,
        "settings": {
            "turn": { "max_iterations": 42 },
            "context": {
                "compaction": { "mode": "automatic", "token_threshold": 120000 }
            },
            "tools": {
                "programmatic": { "max_effects": 64, "max_operations": 512 },
                "large_output": { "mode": "disabled" },
                "subagents": { "mode": "enabled" },
                "background_processes": { "mode": "enabled" },
                "command_environment": { "mode": "unix" }
            }
        },
        "capabilities": {
            "tool_groups": [
                {
                    "name": "clientInteraction",
                    "description": "Request structured participation from the connected client.",
                    "tools": [
                        {
                            "name": "askUser",
                            "description": "Ask the user to choose one of a bounded set of options.",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "question": {"type": "string", "minLength": 1},
                                    "options": {
                                        "type": "array",
                                        "minItems": 2,
                                        "maxItems": 5,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string", "minLength": 1},
                                                "label": {"type": "string", "minLength": 1}
                                            },
                                            "required": ["id", "label"],
                                            "additionalProperties": false
                                        }
                                    }
                                },
                                "required": ["question", "options"],
                                "additionalProperties": false
                            },
                            "output_schema": {
                                "type": "object",
                                "properties": {
                                    "selectedOptionId": {"type": "string"}
                                },
                                "required": ["selectedOptionId"],
                                "additionalProperties": false
                            },
                            "exposure": "direct"
                        }
                    ]
                }
            ],
            "skills": [
                {
                    "name": "code-review",
                    "description": "Review code for correctness, maintainability, and regressions.",
                    "path": "/workspace/.agents/skills/code-review/SKILL.md"
                }
            ],
            "knowledge_folders": [
                {
                    "name": "project-preferences",
                    "description": "Durable user preferences for project planning and reporting.",
                    "path": "/workspace/knowledge/project-preferences/KNOWLEDGE.md",
                    "access": "read_write"
                }
            ],
            "agent_types": [],
            "hook_bindings": [{
                "id": "root-post-llm",
                "point": "post_llm_call",
                "order": 0,
                "selector": {"type": "always"}
            }]
        },
        "plugins": [
            {
                "name": "acme_tools",
                "description": "Acme planning and reporting capabilities.",
                "path": "/plugins/acme.tools",
                "capabilities": {
                    "tool_groups": [
                        {
                            "name": "acmeCalendar",
                            "description": "Read and update Acme calendars.",
                            "metadata": {
                                "type": "connector",
                                "connector_id": "connector-acme-calendar"
                            },
                            "icon_url": "https://assets.example.test/acme-calendar.png",
                            "tools": [
                                {
                                    "name": "listEvents",
                                    "description": "List calendar events in an inclusive time range.",
                                    "input_schema": {
                                        "type": "object",
                                        "properties": {
                                            "calendarId": {"type": "string", "minLength": 1},
                                            "startsAt": {"type": "string", "format": "date-time"},
                                            "endsAt": {"type": "string", "format": "date-time"},
                                            "includeCancelled": {"type": "boolean", "default": false}
                                        },
                                        "required": ["calendarId", "startsAt", "endsAt"],
                                        "additionalProperties": false
                                    },
                                    "output_schema": {
                                        "type": "object",
                                        "properties": {
                                            "events": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "id": {"type": "string"},
                                                        "title": {"type": "string"},
                                                        "startsAt": {"type": "string", "format": "date-time"}
                                                    },
                                                    "required": ["id", "title", "startsAt"],
                                                    "additionalProperties": false
                                                }
                                            }
                                        },
                                        "required": ["events"],
                                        "additionalProperties": false
                                    },
                                    "exposure": "programmatic"
                                },
                                {
                                    "name": "createEvent",
                                    "description": "Create a calendar event and return its stable identity.",
                                    "input_schema": {
                                        "type": "object",
                                        "properties": {
                                            "calendarId": {"type": "string", "minLength": 1},
                                            "title": {"type": "string", "minLength": 1},
                                            "startsAt": {"type": "string", "format": "date-time"},
                                            "durationMinutes": {"type": "integer", "minimum": 1},
                                            "attendees": {
                                                "type": "array",
                                                "items": {"type": "string", "format": "email"},
                                                "default": []
                                            }
                                        },
                                        "required": ["calendarId", "title", "startsAt", "durationMinutes"],
                                        "additionalProperties": false
                                    },
                                    "output_schema": {
                                        "type": "object",
                                        "properties": {
                                            "eventId": {"type": "string"},
                                            "url": {"type": "string", "format": "uri"}
                                        },
                                        "required": ["eventId", "url"],
                                        "additionalProperties": false
                                    },
                                    "exposure": "direct_and_programmatic"
                                }
                            ]
                        }
                    ],
                    "skills": [
                        {
                            "name": "acme_tools:prepare-report",
                            "description": "Prepare an Acme report using approved data sources.",
                            "path": "/plugins/acme.tools/skills/prepare-report/SKILL.md"
                        }
                    ],
                    "knowledge_folders": [
                        {
                            "name": "acme_tools:delivery-policy",
                            "description": "Acme delivery policies and milestone definitions.",
                            "path": "/plugins/acme.tools/knowledge/delivery-policy/KNOWLEDGE.md",
                            "access": "read_write"
                        }
                    ],
                    "agent_types": [
                        {
                            "name": "acme_tools:researcher",
                            "description": "Researches Acme project history and prepares evidence.",
                            "path": "/plugins/acme.tools/agents/researcher.toml"
                        }
                    ],
                    "hook_bindings": [
                        {
                            "id": "acme-pre-tool",
                            "point": "pre_tool_call",
                            "order": 1,
                            "selector": {"type": "always"}
                        },
                        {
                            "id": "acme-post-tool",
                            "point": "post_tool_call",
                            "order": 2,
                            "selector": {"type": "always"}
                        }
                    ]
                }
            }
        ]
    }))
    .unwrap()
}

fn initial_user_message() -> HarnessInput {
    serde_json::from_value(json!({
        "protocol_version": 1,
        "input_id": 1,
        "determinism": {
            "time_unix_ms": 1736942400000_u64,
            "random_seed": 424242
        },
        "command": {
            "type": "user_message",
            "turn_id": "turn-canonical-initial-request",
            "mode": "queue",
            "content": [
                {
                    "type": "text",
                    "text": "Review the project status and schedule the next implementation milestone."
                }
            ]
        }
    }))
    .unwrap()
}

fn initial_llm_call(result: HarnessApplyResult) -> Value {
    let value = serde_json::to_value(result).unwrap();
    let request = &value["transition"]["next"]["directives"][0]["action"];
    assert_eq!(request["type"], "llm_call");
    request.clone()
}

fn reviewable_snapshot(value: Value) -> Value {
    match value {
        Value::String(text) if text.contains('\n') => json!({
            "$multiline": text.split('\n').collect::<Vec<_>>()
        }),
        Value::Array(values) => Value::Array(values.into_iter().map(reviewable_snapshot).collect()),
        Value::Object(fields) => Value::Object(
            fields
                .into_iter()
                .map(|(key, value)| (key, reviewable_snapshot(value)))
                .collect(),
        ),
        value => value,
    }
}
