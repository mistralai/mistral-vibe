use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;

use super::{Checkpoint, decode_checkpoint};
use crate::core::HarnessState;

mod cases;

use cases::fixture_cases;

const CONFIG_SECRET: &str = "fixture configuration must never be checkpointed";
const FIXTURE_DIRECTORY: &str =
    concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/checkpoints/v1");

struct FixtureCase {
    file_name: &'static str,
    state: HarnessState,
}

#[test]
fn checkpoint_v1_fixtures_match_live_semantic_state() {
    let cases = fixture_cases();

    let rendered = cases
        .iter()
        .map(|case| {
            let checkpoint = Checkpoint::capture(&case.state).unwrap();
            let json = format!("{}\n", serde_json::to_string_pretty(&checkpoint).unwrap());
            (case, checkpoint, json)
        })
        .collect::<Vec<_>>();
    if std::env::var("UPDATE_CHECKPOINT_FIXTURES").as_deref() == Ok("1") {
        fs::create_dir_all(FIXTURE_DIRECTORY).unwrap();
        for (case, _, json) in &rendered {
            fs::write(fixture_path(case.file_name), json).unwrap();
        }
    }

    assert_fixture_inventory(&cases);
    for (case, checkpoint, actual) in rendered {
        let expected = fs::read_to_string(fixture_path(case.file_name)).unwrap_or_else(|error| {
            panic!(
                "failed to read checkpoint fixture {}: {error}; run with UPDATE_CHECKPOINT_FIXTURES=1 to create it",
                case.file_name
            )
        });
        assert_eq!(
            actual, expected,
            "checkpoint fixture {} changed",
            case.file_name
        );

        let decoded = decode_checkpoint(&expected)
            .unwrap_or_else(|error| panic!("fixture {} did not decode: {error}", case.file_name));
        let restored = decoded
            .restore(case.state.config.clone())
            .unwrap_or_else(|error| panic!("fixture {} did not restore: {error}", case.file_name));
        assert_eq!(
            Checkpoint::capture(&restored).unwrap(),
            checkpoint,
            "fixture {} changed after decode and restore",
            case.file_name
        );

        let value: Value = serde_json::from_str(&expected).unwrap();
        assert_checkpoint_boundary(case.file_name, &value);
        if case.file_name == "active-tool-batch.json" {
            assert_tool_derived_identities_are_absent(&value);
        }
    }
}

fn fixture_path(file_name: &str) -> PathBuf {
    Path::new(FIXTURE_DIRECTORY).join(file_name)
}

fn assert_fixture_inventory(cases: &[FixtureCase]) {
    let expected = cases
        .iter()
        .map(|case| case.file_name.to_string())
        .collect::<BTreeSet<_>>();
    let actual = fs::read_dir(FIXTURE_DIRECTORY)
        .unwrap_or_else(|error| {
            panic!(
                "failed to read checkpoint fixture directory: {error}; run with UPDATE_CHECKPOINT_FIXTURES=1 to create it"
            )
        })
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .filter(|file_name| file_name != "README.md")
        .collect::<BTreeSet<_>>();
    assert_eq!(actual, expected, "checkpoint fixture inventory changed");
}

fn assert_checkpoint_boundary(file_name: &str, value: &Value) {
    let root = value
        .as_object()
        .unwrap_or_else(|| panic!("fixture {file_name} must be a JSON object"));
    let mut expected_root_keys = BTreeSet::from([
        "checkpoint_version".to_string(),
        "compaction_count".to_string(),
        "context".to_string(),
        "notifications".to_string(),
        "turn".to_string(),
    ]);
    if root.contains_key("last_finished_turn_id") {
        expected_root_keys.insert("last_finished_turn_id".to_string());
    }
    assert_eq!(
        root.keys().cloned().collect::<BTreeSet<_>>(),
        expected_root_keys,
        "fixture {file_name} crossed the semantic checkpoint root boundary"
    );

    let context = root["context"]
        .as_object()
        .unwrap_or_else(|| panic!("fixture {file_name} context must be an object"));
    assert_eq!(
        context.keys().cloned().collect::<BTreeSet<_>>(),
        BTreeSet::from(["messages".to_string()]),
        "fixture {file_name} persisted configuration or disposable model-cache state in context"
    );

    let messages = context["messages"]
        .as_array()
        .unwrap_or_else(|| panic!("fixture {file_name} context messages must be an array"));
    for stored in messages {
        assert!(
            matches!(stored["source"].as_str(), Some("history" | "injection")),
            "fixture {file_name} persisted a generated Core message"
        );
    }
    assert!(
        !serde_json::to_string(messages)
            .expect("checkpoint messages serialize")
            .contains(CONFIG_SECRET),
        "fixture {file_name} leaked the current configuration's generated system prompt"
    );
}

fn assert_tool_derived_identities_are_absent(value: &Value) {
    let completed_model_call = value["context"]["messages"]
        .as_array()
        .and_then(|messages| messages.last())
        .and_then(|stored| stored["message"]["content"].as_array())
        .and_then(|parts| parts.iter().find(|part| part["id"] == "direct-completed"))
        .expect("completed fixture call must remain in assistant history");
    assert_eq!(
        completed_model_call["arguments"]["value"]["opaque_payload"]["action_id"], "user data",
        "open JSON payloads must be allowed to use names reserved by checkpoint structure"
    );

    let executions = value["turn"]["phase"]["batch"]["executions"]
        .as_array()
        .expect("tool-batch fixture executions must be an array");
    for execution in executions {
        let state = execution
            .as_object()
            .expect("tool execution state must be an object");
        assert_field_absent(state, "state", "tool execution state");
        assert_field_absent(state, "id", "tool execution state");
        assert_field_absent(state, "name", "tool execution state");
        assert_field_absent(state, "arguments", "tool execution state");
        match state["type"].as_str().expect("tool state type") {
            "direct_awaiting_pre_hook" | "direct_awaiting_post_hook" => {
                assert_field_absent(state, "hook_action_id", "direct tool hook state");
                assert_external_call_has_no_derived_identity(&state["call"]);
            }
            "direct_pending" => assert_external_call_has_no_derived_identity(&state["call"]),
            "program_pending" => {
                let program = state["execution"]
                    .as_object()
                    .expect("program execution must be an object");
                assert_field_absent(program, "code", "program execution");
                assert_field_absent(program, "input", "program execution");
                let operations = state["execution"]["operations"]
                    .as_array()
                    .expect("program operations must be an array");
                for operation in operations {
                    if operation["type"] == "internal" {
                        assert_field_absent(
                            operation
                                .as_object()
                                .expect("internal operation must be an object"),
                            "function",
                            "internal program operation",
                        );
                        continue;
                    }
                    let external = operation["execution"]
                        .as_object()
                        .expect("external program execution must be an object");
                    match external["type"].as_str().expect("external execution type") {
                        "awaiting_pre_hook" | "awaiting_post_hook" => {
                            assert_field_absent(
                                external,
                                "hook_action_id",
                                "program tool hook state",
                            );
                            assert_external_call_has_no_derived_identity(&external["call"]);
                        }
                        "pending" => {
                            assert_external_call_has_no_derived_identity(&external["call"]);
                        }
                        "resolved" | "rejected" => {}
                        state => panic!("unexpected external program fixture state {state:?}"),
                    }
                }
            }
            "completed" => {}
            state => panic!("unexpected tool fixture state {state:?}"),
        }
    }
}

fn assert_external_call_has_no_derived_identity(value: &Value) {
    let call = value
        .as_object()
        .expect("checkpoint external call must be an object");
    for field in ["action_id", "call_id", "origin"] {
        assert_field_absent(call, field, "checkpoint external call");
    }
}

fn assert_field_absent(object: &serde_json::Map<String, Value>, field: &str, label: &str) {
    assert!(!object.contains_key(field), "{label} contains {field:?}");
}
