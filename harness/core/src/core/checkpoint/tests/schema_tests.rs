use serde_json::{Value, json};

use super::cases::*;
use crate::core::checkpoint::{Checkpoint, decode_checkpoint};
use crate::core::testing::config;

#[test]
fn decode_rejects_an_unsupported_version_before_reading_its_payload() {
    let checkpoint = json!({
        "checkpoint_version": 4,
        "future_state": { "layout": "does-not-match-v1" }
    });

    let error = decode_checkpoint(&checkpoint.to_string()).unwrap_err();

    assert_eq!(error, "unsupported session checkpoint version 4");
}

#[test]
fn restore_rejects_duplicate_notification_identities_across_the_wire_boundary() {
    let mut checkpoint = checkpoint_with_turn(json!({ "type": "idle" }));
    checkpoint["notifications"] = json!([
        {
            "type": "pending",
            "notification": {
                "id": "notification-1",
                "source": {
                    "type": "async_tool",
                    "call_id": "call-1",
                    "status": "completed"
                },
                "level": "info",
                "message": "done"
            }
        },
        { "type": "received", "id": "notification-1" }
    ]);

    let decoded = decode_checkpoint(&checkpoint.to_string()).unwrap();
    let error = decoded.restore(config()).unwrap_err();

    assert!(error.contains("duplicate notification ID"));
}

#[test]
fn decode_validates_the_payload_for_the_current_version() {
    let checkpoint = json!({
        "checkpoint_version": 1,
        "future_state": { "layout": "does-not-match-v1" }
    });

    let error = decode_checkpoint(&checkpoint.to_string()).unwrap_err();

    assert!(error.contains("unknown field `future_state`"));
}

#[test]
fn decode_rejects_unknown_fields_in_owned_nested_leaves() {
    let mut message_envelope = checkpoint_with_turn(json!({ "type": "idle" }));
    message_envelope["context"]["messages"] = json!([{
        "message": {
            "role": "user",
            "content": [{ "type": "text", "text": "hello" }],
            "future": true
        },
        "source": "history"
    }]);

    let mut assistant_arguments = checkpoint_with_turn(json!({ "type": "idle" }));
    assistant_arguments["context"]["messages"] = json!([{
        "message": {
            "role": "assistant",
            "content": [{
                "type": "tool_call",
                "id": "call-1",
                "name": "lookup",
                "arguments": {
                    "type": "json",
                    "raw": "{}",
                    "value": {},
                    "future": true
                }
            }]
        },
        "source": "history"
    }]);

    let tool = active_tool_checkpoint();
    let mut external_call = tool.clone();
    external_call["turn"]["phase"]["batch"]["executions"][0]["call"]["future"] = json!(true);
    let mut tool_result = tool;
    tool_result["turn"]["phase"]["batch"]["executions"][0]["result"]["future"] = json!(true);
    let mut protocol_error = active_tool_checkpoint();
    protocol_error["turn"]["phase"]["batch"]["executions"][0]["result"] = json!({
        "type": "failure",
        "content": [],
        "error": {
            "code": "tool_failed",
            "message": "failed",
            "retryable": false,
            "details": {},
            "future": true
        }
    });

    let mut candidate = active_candidate_checkpoint();
    candidate["turn"]["phase"]["pending"]["candidate"]["future"] = json!(true);
    let mut token_usage = active_candidate_checkpoint();
    token_usage["turn"]["phase"]["pending"]["candidate"]["usage"] = json!({
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
        "future": true
    });

    let mut function = restorable_pending_program_checkpoint();
    function["turn"]["phase"]["batch"]["executions"][0]["execution"]["operations"][0]["function"]
        ["future"] = json!(true);

    let mut notification = checkpoint_with_turn(json!({ "type": "idle" }));
    notification["notifications"] = json!([{
        "type": "pending",
        "notification": {
            "id": "notification-1",
            "source": {
                "type": "async_tool",
                "call_id": "call-1",
                "status": "completed"
            },
            "level": "info",
            "message": "done",
            "future": true
        }
    }]);
    let mut notification_source = notification.clone();
    notification_source["notifications"][0]["notification"]["source"]["future"] = json!(true);

    for (label, checkpoint) in [
        ("message", message_envelope),
        ("assistant tool arguments", assistant_arguments),
        ("external call", external_call),
        ("tool result", tool_result),
        ("protocol error", protocol_error),
        ("completion candidate", candidate),
        ("completion token usage", token_usage),
        ("program function", function),
        ("notification", notification),
        ("notification source", notification_source),
    ] {
        assert!(
            decode_checkpoint(&checkpoint.to_string()).is_err(),
            "{label} accepted an unknown field"
        );
    }
}

#[test]
fn decode_uses_standard_mcp_content_field_names_and_extension_tolerance() {
    let mut checkpoint = checkpoint_with_turn(json!({ "type": "idle" }));
    checkpoint["context"]["messages"] = json!([{
        "message": {
            "role": "user",
            "content": [{
                "type": "image",
                "data": "aW1hZ2U=",
                "mimeType": "image/png",
                "annotations": {
                    "audience": ["user"],
                    "lastModified": "2026-08-11T00:00:00Z",
                    "future": true
                },
                "future": true
            }]
        },
        "source": "history"
    }]);

    let decoded = decode_checkpoint(&checkpoint.to_string()).unwrap();
    let encoded = serde_json::to_value(decoded).unwrap();

    assert_eq!(
        encoded["context"]["messages"][0]["message"]["content"][0]["mimeType"],
        "image/png"
    );
    assert!(
        encoded["context"]["messages"][0]["message"]["content"][0]
            .get("future")
            .is_none()
    );
}

#[test]
fn decode_rejects_unknown_nested_discriminants() {
    let mut content = checkpoint_with_turn(json!({ "type": "idle" }));
    content["context"]["messages"] = json!([{
        "message": {
            "role": "user",
            "content": [{ "type": "future_content", "value": true }]
        },
        "source": "history"
    }]);

    let mut external_call = active_tool_checkpoint();
    external_call["turn"]["phase"]["batch"]["executions"][0]["call"]["type"] = json!("future_tool");

    let mut notification = checkpoint_with_turn(json!({ "type": "idle" }));
    notification["notifications"] = json!([{
        "type": "pending",
        "notification": {
            "id": "notification-1",
            "source": { "type": "future_source" },
            "level": "info",
            "message": "done"
        }
    }]);

    let mut assistant_part = checkpoint_with_turn(json!({ "type": "idle" }));
    assistant_part["context"]["messages"] = json!([{
        "message": {
            "role": "assistant",
            "content": [{ "type": "future_assistant_part", "value": true }]
        },
        "source": "history"
    }]);

    let mut tool_arguments = checkpoint_with_turn(json!({ "type": "idle" }));
    tool_arguments["context"]["messages"] = json!([{
        "message": {
            "role": "assistant",
            "content": [{
                "type": "tool_call",
                "id": "call-1",
                "name": "tool",
                "arguments": { "type": "future_arguments" }
            }]
        },
        "source": "history"
    }]);

    for (label, checkpoint) in [
        ("content block", content),
        ("external tool", external_call),
        ("notification source", notification),
        ("assistant part", assistant_part),
        ("tool arguments", tool_arguments),
    ] {
        assert!(
            decode_checkpoint(&checkpoint.to_string()).is_err(),
            "{label} accepted a non-V1 representation"
        );
    }
}

#[test]
fn decode_keeps_json_payloads_and_meta_maps_open() {
    let mut checkpoint = restorable_pending_program_checkpoint();
    checkpoint["context"]["messages"] = json!([{
        "message": {
            "role": "user",
            "content": [{
                "type": "text",
                "text": "hello",
                "_meta": {
                    "vendor_extension": { "nested": [1, true, null] }
                }
            }]
        },
        "source": "history"
    }]);
    checkpoint["turn"]["phase"]["batch"]["executions"][0]["execution"]["operations"][0]["function"]
        ["arguments"] = json!({
        "arbitrary": { "nested": [1, true, null] }
    });

    decode_checkpoint(&checkpoint.to_string()).unwrap();

    let mut tool_result = active_tool_checkpoint();
    tool_result["turn"]["phase"]["batch"]["executions"][0]["result"]["_meta"] =
        json!({ "vendor_extension": { "nested": [1, true, null] } });
    tool_result["turn"]["phase"]["batch"]["executions"][0]["result"]["structured_content"] =
        json!({ "arbitrary": { "nested": [1, true, null] } });

    decode_checkpoint(&tool_result.to_string()).unwrap();
}

#[test]
fn tool_result_checkpoint_distinguishes_absent_and_null_structured_content() {
    let absent = restorable_active_tool_checkpoint();
    let decoded_absent = decode_checkpoint(&absent.to_string()).unwrap();
    let encoded_absent = serde_json::to_value(&decoded_absent).unwrap();
    assert!(
        encoded_absent["turn"]["phase"]["batch"]["executions"][0]["result"]
            .get("structured_content")
            .is_none()
    );
    let restored_absent = decoded_absent.restore(config()).unwrap();
    let recaptured_absent = Checkpoint::capture(&restored_absent).unwrap();
    assert!(
        serde_json::to_value(recaptured_absent).unwrap()["turn"]["phase"]["batch"]["executions"][0]
            ["result"]
            .get("structured_content")
            .is_none()
    );

    let mut explicit_null = absent;
    explicit_null["turn"]["phase"]["batch"]["executions"][0]["result"]["structured_content"] =
        Value::Null;
    let decoded_null = decode_checkpoint(&explicit_null.to_string()).unwrap();
    let encoded_null = serde_json::to_value(&decoded_null).unwrap();
    assert_eq!(
        encoded_null["turn"]["phase"]["batch"]["executions"][0]["result"]["structured_content"],
        Value::Null
    );
    let restored_null = decoded_null.restore(config()).unwrap();
    let recaptured_null = Checkpoint::capture(&restored_null).unwrap();
    assert_eq!(
        serde_json::to_value(recaptured_null).unwrap()["turn"]["phase"]["batch"]["executions"][0]["result"]
            ["structured_content"],
        Value::Null
    );
}

#[test]
fn decode_rejects_duplicate_fields_inside_owned_union_leaves() {
    let checkpoint = r#"{
        "checkpoint_version": 1,
        "compaction_count": 0,
        "context": {
            "messages": [{
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "text",
                        "text": "first",
                        "text": "second"
                    }]
                },
                "source": "history"
            }]
        },
        "turn": { "type": "idle" },
        "notifications": []
    }"#;

    assert!(decode_checkpoint(checkpoint).is_err());
}

#[test]
fn checkpoint_round_trips_every_rich_content_shape_without_semantic_loss() {
    let mut checkpoint = checkpoint_with_turn(json!({ "type": "idle" }));
    checkpoint["context"]["messages"] = json!([
        {
            "message": {
                "role": "user",
                "content": rich_content()
            },
            "source": "history"
        },
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "reasoning",
                        "content": [
                            { "type": "text", "text": "thinking" },
                            { "type": "summary", "text": "summary" },
                            { "type": "redacted", "data": "cmVkYWN0ZWQ=" }
                        ],
                        "_meta": { "provider": { "trace": [1, true, null] } }
                    },
                    {
                        "type": "tool_call",
                        "id": "call-1",
                        "name": "lookup",
                        "arguments": {
                            "type": "json",
                            "raw": "{\"query\":\"rust\"}",
                            "value": { "query": "rust" }
                        },
                        "_meta": { "provider": { "opaque": true } }
                    },
                    { "type": "text", "text": "working" }
                ]
            },
            "source": "injection"
        },
        {
            "message": {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "lookup",
                "outcome": "success",
                "content": [{ "type": "text", "text": "done" }],
                "_meta": { "client": { "request_id": "request-1" } }
            },
            "source": "history"
        }
    ]);

    let decoded = decode_checkpoint(&checkpoint.to_string()).unwrap();
    assert_eq!(serde_json::to_value(&decoded).unwrap(), checkpoint);

    let restored = decoded.restore(config()).unwrap();
    let recaptured = Checkpoint::capture(&restored).unwrap();
    assert_eq!(serde_json::to_value(recaptured).unwrap(), checkpoint);
}

#[test]
fn decode_rejects_resource_contents_without_a_payload() {
    let mut checkpoint = checkpoint_with_turn(json!({ "type": "idle" }));
    checkpoint["context"]["messages"] = json!([{
        "message": {
            "role": "user",
            "content": [{
                "type": "resource",
                "resource": {
                    "uri": "resource://missing-payload"
                }
            }]
        },
        "source": "history"
    }]);

    assert!(decode_checkpoint(&checkpoint.to_string()).is_err());
}

#[test]
fn restore_rejects_completion_candidates_that_core_cannot_create() {
    let base = restorable_active_candidate_checkpoint();

    let mut empty = base.clone();
    empty["turn"]["phase"]["pending"]["candidate"]["message"]["content"] = json!([]);

    let mut tool_finish_without_call = base.clone();
    tool_finish_without_call["turn"]["phase"]["pending"]["candidate"]["finish_reason"] =
        json!("tool_call");

    let mut call_with_stop = base.clone();
    call_with_stop["turn"]["phase"]["pending"]["candidate"]["message"]["content"] =
        json!([valid_candidate_tool_call("call-1")]);

    let mut duplicate_calls = base.clone();
    duplicate_calls["turn"]["phase"]["pending"]["candidate"]["message"]["content"] = json!([
        valid_candidate_tool_call("call-1"),
        valid_candidate_tool_call("call-1")
    ]);
    duplicate_calls["turn"]["phase"]["pending"]["candidate"]["finish_reason"] = json!("tool_call");

    let mut mismatched_json = base.clone();
    mismatched_json["turn"]["phase"]["pending"]["candidate"]["message"]["content"] = json!([{
        "type": "tool_call",
        "id": "call-1",
        "name": "lookup",
        "arguments": {
            "type": "json",
            "raw": "{\"query\":\"rust\"}",
            "value": { "query": "typescript" }
        }
    }]);
    mismatched_json["turn"]["phase"]["pending"]["candidate"]["finish_reason"] = json!("tool_call");

    let mut valid_marked_invalid = base.clone();
    valid_marked_invalid["turn"]["phase"]["pending"]["candidate"]["message"]["content"] = json!([{
        "type": "tool_call",
        "id": "call-1",
        "name": "lookup",
        "arguments": {
            "type": "invalid_json",
            "raw": "{}",
            "error": "expected invalid JSON"
        }
    }]);
    valid_marked_invalid["turn"]["phase"]["pending"]["candidate"]["finish_reason"] =
        json!("tool_call");

    let mut invalid_usage = base.clone();
    invalid_usage["turn"]["phase"]["pending"]["candidate"]["usage"] = json!({
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 4
    });

    let mut invalid_cached_usage = base;
    invalid_cached_usage["turn"]["phase"]["pending"]["candidate"]["usage"] = json!({
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
        "cached_input_tokens": 2
    });

    for (label, checkpoint) in [
        ("empty assistant message", empty),
        ("tool finish without call", tool_finish_without_call),
        ("tool call with stop finish", call_with_stop),
        ("duplicate tool calls", duplicate_calls),
        ("mismatched parsed arguments", mismatched_json),
        ("valid JSON marked invalid", valid_marked_invalid),
        ("inconsistent token usage", invalid_usage),
        ("cached usage exceeding input", invalid_cached_usage),
    ] {
        let checkpoint = decode_checkpoint(&checkpoint.to_string()).unwrap();
        assert!(
            checkpoint.restore(config()).is_err(),
            "restored invalid candidate: {label}"
        );
    }
}

///
/// *Prepare*: A checkpoint contains a pending completion candidate with reasoning and no visible content or tool call.
/// *Do*: Decode, restore, and recapture the checkpoint.
/// *Assert*: The reasoning-only candidate survives without semantic changes.
///
#[test]
fn checkpoint_round_trips_a_reasoning_only_completion_candidate() {
    // Prepare
    let mut checkpoint = restorable_active_candidate_checkpoint();
    checkpoint["turn"]["phase"]["pending"]["candidate"]["message"]["content"] = json!([{
        "type": "reasoning",
        "content": [{"type": "text", "text": "The request is complete."}],
    }]);

    // Do
    let decoded = decode_checkpoint(&checkpoint.to_string()).unwrap();
    let restored = decoded.restore(config()).unwrap();
    let recaptured = Checkpoint::capture(&restored).unwrap();
    let recaptured = serde_json::to_value(recaptured).unwrap();

    // Assert
    assert_eq!(
        recaptured["turn"]["phase"]["pending"]["candidate"],
        checkpoint["turn"]["phase"]["pending"]["candidate"],
    );
}

#[test]
fn restore_defaults_cached_input_tokens_for_older_checkpoints() {
    let mut checkpoint = restorable_active_candidate_checkpoint();
    checkpoint["turn"]["phase"]["pending"]["candidate"]["usage"] = json!({
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12
    });

    let decoded = decode_checkpoint(&checkpoint.to_string()).unwrap();
    let restored = decoded.restore(config()).unwrap();
    let recaptured = serde_json::to_value(Checkpoint::capture(&restored).unwrap()).unwrap();

    assert_eq!(
        recaptured["turn"]["phase"]["pending"]["candidate"]["usage"]["cached_input_tokens"],
        0
    );
}

#[test]
fn checkpoint_round_trips_a_valid_invalid_json_completion_candidate() {
    let mut checkpoint = restorable_active_candidate_checkpoint();
    checkpoint["turn"]["phase"]["pending"]["candidate"]["message"]["content"] = json!([{
        "type": "tool_call",
        "id": "call-1",
        "name": "lookup",
        "arguments": {
            "type": "invalid_json",
            "raw": "{",
            "error": "EOF while parsing an object at line 1 column 1"
        }
    }]);
    checkpoint["turn"]["phase"]["pending"]["candidate"]["finish_reason"] = json!("tool_call");

    let decoded = decode_checkpoint(&checkpoint.to_string()).unwrap();
    let restored = decoded.restore(config()).unwrap();
    let recaptured = Checkpoint::capture(&restored).unwrap();

    assert_eq!(serde_json::to_value(recaptured).unwrap(), checkpoint);
}

#[test]
fn restore_rejects_invalid_pre_agent_content_and_pending_notifications() {
    let pre_agent = checkpoint_with_turn(json!({
        "type": "active",
        "turn_id": "turn-1",
        "iterations": 0,
        "phase": {
            "type": "awaiting_hook",
            "pending": {
                "hook": "pre_agent_turn",
                "hook_binding_ids": ["binding-1"],
                "user_content": [],
                "previous_turn": { "type": "idle" }
            }
        },
        "queued": { "type": "empty" },
        "pending_steer": [],
        "model_input_ready": false
    }));

    let mut notification = checkpoint_with_turn(json!({ "type": "idle" }));
    notification["notifications"] = json!([{
        "type": "pending",
        "notification": {
            "id": "notification-1",
            "source": {
                "type": "async_tool",
                "call_id": "",
                "status": "completed"
            },
            "level": "info",
            "message": ""
        }
    }]);

    for (label, checkpoint) in [
        ("empty pre-agent content", pre_agent),
        ("invalid pending notification", notification),
    ] {
        let checkpoint = decode_checkpoint(&checkpoint.to_string()).unwrap();
        assert!(checkpoint.restore(config()).is_err(), "restored {label}");
    }
}
