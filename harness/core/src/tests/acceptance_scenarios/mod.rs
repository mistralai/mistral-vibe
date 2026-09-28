//! End-to-end acceptance sequences requested for the serialized Core boundary.
//! Each command is applied through a synchronous Runtime driver that consumes
//! every directive and asserts the complete observation list and turn state.

use super::*;

mod async_features;
mod compaction;
mod completion_and_tools;
mod hooks;
mod large_output;
mod recovery;
mod runtime_driver;
mod steering;
mod tool_argument_validation;
mod turn_lifecycle;

use runtime_driver::*;

fn tool_call(id: &str, name: &str, arguments: Value) -> Value {
    json!({
        "type": "tool_call",
        "id": id,
        "name": name,
        "arguments_json": arguments.to_string(),
    })
}

fn tool_call_completion(action_id: &str, calls: Vec<Value>) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id,
        "result": {
            "parts": calls,
            "finish_reason": "tool_call",
            "usage": null,
        },
    })
}

fn run_typescript_completion(action_id: &str, call_id: &str, source: &str) -> Value {
    tool_call_completion(
        action_id,
        vec![tool_call(
            call_id,
            "run_typescript",
            json!({"code": source}),
        )],
    )
}

fn text_completion(action: &Value, text: &str) -> Value {
    completion(action_id(action), json!([{"type": "text", "text": text}]))
}

fn text_tool_success(action: &Value, text: &str) -> Value {
    json!({
        "type": "tool_succeeded",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": {
            "type": "success",
            "content": [{"type": "text", "text": text}],
        },
    })
}

fn structured_tool_success(action: &Value, structured_content: Value) -> Value {
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

fn run_typescript_success(structured_content: Value) -> Value {
    json!({
        "type": "success",
        "content": [{
            "type": "text",
            "text": serde_json::to_string(&structured_content)
                .expect("run_typescript result serializes"),
        }],
        "structured_content": structured_content,
    })
}

fn file_read_success(action: &Value, content: &str) -> Value {
    structured_tool_success(
        action,
        json!({
            "path": action["call"]["arguments"]["path"],
            "content": content,
            "file_size_bytes": content.len(),
            "returned_bytes": content.len(),
            "offset": 0,
            "lines_read": 1,
            "was_truncated": false,
        }),
    )
}

fn hook_completed(action: &Value, hook: &str, output: Value) -> Value {
    json!({
        "type": "hook_completed",
        "action_id": action["action_id"],
        "result": {
            "hook": hook,
            "output": output,
        },
    })
}

fn turn_started(turn_id: &str, text: &str) -> Value {
    json!({
        "type": "turn_started",
        "turn_id": turn_id,
        "content": [{"type": "text", "text": text}],
    })
}

fn turn_steered(turn_id: &str, text: &str) -> Value {
    json!({
        "type": "turn_steered",
        "turn_id": turn_id,
        "content": [{"type": "text", "text": text}],
    })
}

fn turn_steering_received(turn_id: &str, text: &str) -> Value {
    json!({
        "type": "turn_steering_received",
        "turn_id": turn_id,
        "content": [{"type": "text", "text": text}],
    })
}

fn assistant_text_committed(turn_id: &str, action: &Value, text: &str) -> Value {
    json!({
        "type": "assistant_message_committed",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "candidate": text_candidate(text),
    })
}

fn text_candidate(text: &str) -> Value {
    json!({
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
        "finish_reason": "stop",
        "usage": null,
    })
}

fn tool_calls_candidate(calls: &[Value]) -> Value {
    let content = calls
        .iter()
        .map(|call| {
            let raw = call["arguments_json"]
                .as_str()
                .expect("tool call has serialized arguments");
            json!({
                "type": "tool_call",
                "id": call["id"],
                "name": call["name"],
                "arguments": {
                    "type": "json",
                    "raw": raw,
                    "value": serde_json::from_str::<Value>(raw).expect("tool call arguments are JSON"),
                },
            })
        })
        .collect::<Vec<_>>();
    json!({
        "message": {"role": "assistant", "content": content},
        "finish_reason": "tool_call",
        "usage": null,
    })
}

fn assistant_tool_calls_committed(turn_id: &str, action: &Value, calls: &[Value]) -> Value {
    json!({
        "type": "assistant_message_committed",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "candidate": tool_calls_candidate(calls),
    })
}

fn tool_result_committed(turn_id: &str, action: &Value, result: Value) -> Value {
    json!({
        "type": "tool_result_committed",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "call_id": action["call_id"],
        "result": result,
    })
}

fn tool_execution_started(turn_id: &str, call_id: &str) -> Value {
    json!({
        "type": "tool_execution_started",
        "turn_id": turn_id,
        "call_id": call_id,
    })
}

fn tool_execution_finished(turn_id: &str, call_id: &str, result: Value) -> Value {
    json!({
        "type": "tool_execution_finished",
        "turn_id": turn_id,
        "call_id": call_id,
        "result": result,
    })
}

fn notification_received(turn_id: &str, notification: Value) -> Value {
    json!({
        "type": "notification_received",
        "turn_id": turn_id,
        "notification": notification,
    })
}

fn notification_delivered(turn_id: &str, notification: Value) -> Value {
    json!({
        "type": "notification_delivered",
        "turn_id": turn_id,
        "notification": notification,
    })
}

fn observed_notification(command: &Value) -> Value {
    let mut notification = command["notification"].clone();
    if notification["content"] == json!([]) {
        notification
            .as_object_mut()
            .expect("notification is an object")
            .remove("content");
    }
    notification
}

fn turn_completed(turn_id: &str, text: &str) -> Value {
    json!({
        "type": "turn_completed",
        "turn_id": turn_id,
        "output": [{"type": "text", "text": text}],
    })
}

fn model_user_text(text: &str) -> Value {
    json!({
        "role": "user",
        "content": [{"type": "text", "text": text}],
    })
}

fn model_system() -> Value {
    json!({"role": "system"})
}

fn model_assistant_text(text: &str) -> Value {
    json!({
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
    })
}

fn model_assistant_tool_calls(calls: &[Value]) -> Value {
    tool_calls_candidate(calls)["message"].clone()
}

fn model_assistant_typescript(call_id: &str, source: &str) -> Value {
    model_assistant_tool_calls(&[tool_call(
        call_id,
        "run_typescript",
        json!({"code": source}),
    )])
}

fn agent_completion_candidate_discarded(turn_id: &str, action: &Value, cause: &str) -> Value {
    json!({
        "type": "agent_completion_candidate_discarded",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "cause": {"type": cause},
    })
}

fn turn_interrupted(turn_id: &str, reason: &str) -> Value {
    json!({
        "type": "turn_interrupted",
        "turn_id": turn_id,
        "reason": reason,
    })
}

fn action_abandoned(turn_id: &str, action: &Value, cause: &str) -> Value {
    json!({
        "type": "action_abandoned",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "cause": cause,
    })
}

fn model_tool_text(call_id: &str, name: &str, text: &str) -> Value {
    json!({
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "outcome": "success",
        "content": [{"type": "text", "text": text}],
    })
}

fn model_tool(call_id: &str, name: &str) -> Value {
    json!({
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "outcome": "success",
    })
}

fn model_invalid_tool_call(call_id: &str, name: &str, reason: &str) -> Value {
    json!({
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "outcome": "failure",
        "content": [{
            "type": "text",
            "text": json!({
                "result": {
                    "type": "error",
                    "error": {
                        "name": "InvalidToolCall",
                        "message": reason,
                    },
                },
            }).to_string(),
        }],
    })
}

fn invalid_tool_call_result(reason: &str) -> Value {
    json!({
        "type": "failure",
        "content": [{
            "type": "text",
            "text": json!({
                "result": {
                    "type": "error",
                    "error": {
                        "name": "InvalidToolCall",
                        "message": reason,
                    },
                },
            }).to_string(),
        }],
        "error": {
            "code": "invalid_tool_call",
            "message": reason,
            "retryable": false,
            "details": null,
        },
    })
}

fn model_notifications(commands: &[&Value]) -> Value {
    let content = commands
        .iter()
        .map(|command| {
            let notification = &command["notification"];
            let summary = json!({
                "id": notification["id"],
                "source": notification["source"],
                "level": notification["level"],
                "message": notification["message"],
            });
            json!({
                "type": "text",
                "text": format!("Runtime notification:\n{summary}"),
            })
        })
        .collect::<Vec<_>>();
    json!({"role": "user", "content": content})
}
