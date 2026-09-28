use serde_json::{Value, json};

pub(super) fn checkpoint_with_turn(turn: Value) -> Value {
    json!({
        "checkpoint_version": 1,
        "compaction_count": 0,
        "context": {
            "messages": []
        },
        "turn": turn,
        "notifications": []
    })
}

pub(super) fn test_completion_action_id() -> String {
    crate::core::action_id::completion("checkpoint-test", 0, 1)
}

pub(super) fn test_compaction_action_id() -> String {
    crate::core::action_id::compaction("checkpoint-test", 1)
}

pub(super) fn active_tool_checkpoint() -> Value {
    checkpoint_with_turn(json!({
        "type": "active",
        "turn_id": "turn-1",
        "iterations": 1,
        "phase": {
            "type": "awaiting_tool_batch",
            "batch": {
                "executions": [{
                    "type": "direct_awaiting_post_hook",
                    "hook_binding_ids": ["binding-1"],
                    "call": {
                        "type": "provided",
                        "group_name": "group",
                        "tool_name": "tool",
                        "arguments": {}
                    },
                    "result": {
                        "type": "success",
                        "content": []
                    }
                }]
            }
        },
        "queued": { "type": "empty" },
        "pending_steer": [],
        "model_input_ready": false
    }))
}

pub(super) fn restorable_active_tool_checkpoint() -> Value {
    let mut checkpoint = active_tool_checkpoint();
    checkpoint["context"]["messages"] = json!([
        {
            "message": {
                "role": "user",
                "content": [{ "type": "text", "text": "hello" }]
            },
            "source": "history"
        },
        {
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "tool",
                    "arguments": {
                        "type": "json",
                        "raw": "{}",
                        "value": {}
                    }
                }]
            },
            "source": "history"
        }
    ]);
    checkpoint
}

pub(super) fn active_candidate_checkpoint() -> Value {
    checkpoint_with_turn(json!({
        "type": "active",
        "turn_id": "turn-1",
        "iterations": 1,
        "phase": {
            "type": "awaiting_hook",
            "pending": {
                "hook": "post_llm_call",
                "hook_binding_ids": ["binding-1"],
                "completion_action_id": test_completion_action_id(),
                "candidate": {
                    "message": {
                        "role": "assistant",
                        "content": [{ "type": "text", "text": "done" }]
                    },
                    "finish_reason": "stop",
                    "usage": null
                }
            }
        },
        "queued": { "type": "empty" },
        "pending_steer": [],
        "model_input_ready": false
    }))
}

pub(super) fn restorable_active_candidate_checkpoint() -> Value {
    let mut checkpoint = active_candidate_checkpoint();
    checkpoint["context"]["messages"] = json!([{
        "message": {
            "role": "user",
            "content": [{ "type": "text", "text": "hello" }]
        },
        "source": "history"
    }]);
    checkpoint
}

pub(super) fn valid_candidate_tool_call(id: &str) -> Value {
    json!({
        "type": "tool_call",
        "id": id,
        "name": "lookup",
        "arguments": {
            "type": "json",
            "raw": "{\"query\":\"rust\"}",
            "value": { "query": "rust" }
        }
    })
}

pub(super) fn rich_content() -> Value {
    json!([
        {
            "type": "text",
            "text": "text",
            "annotations": {
                "audience": ["assistant", "user"],
                "priority": 0.75,
                "lastModified": "2026-08-09T12:00:00Z"
            },
            "_meta": { "text_extension": { "nested": [1, true, null] } }
        },
        {
            "type": "image",
            "data": "aW1hZ2U=",
            "mimeType": "image/png",
            "_meta": { "image_extension": true }
        },
        {
            "type": "audio",
            "data": "YXVkaW8=",
            "mimeType": "audio/wav",
            "annotations": { "audience": ["user"] }
        },
        {
            "type": "resource_link",
            "uri": "resource://report",
            "name": "report.pdf",
            "title": "Report",
            "description": "A report",
            "mimeType": "application/pdf",
            "size": 42,
            "icons": [{
                "src": "resource://report-icon",
                "mimeType": "image/png",
                "sizes": ["48x48"],
                "theme": "dark"
            }],
            "_meta": { "link_extension": ["opaque"] }
        },
        {
            "type": "resource",
            "resource": {
                "uri": "resource://notes",
                "mimeType": "text/plain",
                "text": "notes",
                "_meta": { "resource_extension": { "text": true } }
            },
            "annotations": { "priority": 0.5 }
        },
        {
            "type": "resource",
            "resource": {
                "uri": "resource://archive",
                "mimeType": "application/octet-stream",
                "blob": "YmxvYg==",
                "_meta": { "resource_extension": { "blob": true } }
            },
            "_meta": { "block_extension": true }
        }
    ])
}

pub(super) fn restorable_pending_program_checkpoint() -> Value {
    let code = "return tools.file_system.read_file({ path: 'artifact.txt' })";
    let mut checkpoint = checkpoint_with_turn(json!({
        "type": "active",
        "turn_id": "turn-1",
        "iterations": 1,
        "phase": {
            "type": "awaiting_tool_batch",
            "batch": {
                "executions": [{
                    "type": "program_pending",
                    "execution": {
                        "operations": [{
                            "type": "external",
                            "id": "operation-1",
                            "function": {
                                "name": "read_file",
                                "arguments": { "path": "artifact.txt" }
                            },
                            "execution": {
                                "type": "awaiting_pre_hook",
                                "hook_binding_ids": ["binding-1"],
                                "call": {
                                    "type": "runtime_builtin",
                                    "name": "file_system.read_file",
                                    "arguments": { "path": "artifact.txt" }
                                }
                            }
                        }],
                        "round": 1
                    }
                }]
            }
        },
        "queued": { "type": "empty" },
        "pending_steer": [],
        "model_input_ready": false
    }));
    checkpoint["context"]["messages"] = json!([
        {
            "message": {
                "role": "user",
                "content": [{ "type": "text", "text": "hello" }]
            },
            "source": "history"
        },
        {
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_call",
                    "id": "run-1",
                    "name": "run_typescript",
                    "arguments": {
                        "type": "json",
                        "raw": serde_json::to_string(&json!({ "code": code })).unwrap(),
                        "value": { "code": code }
                    }
                }]
            },
            "source": "history"
        }
    ]);
    checkpoint
}
