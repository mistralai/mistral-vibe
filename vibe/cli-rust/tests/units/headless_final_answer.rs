//! Tests for the answer a headless text run prints: the server's, else the history's.
use serde_json::json;
use vibe_rs::cli::OutputFormat;
use vibe_rs::headless_output::Output;
use vibe_rs::server::PublicTurn;

fn turn(fields: serde_json::Value) -> PublicTurn {
    let mut turn = json!({"id": "t1", "status": "completed"});
    turn.as_object_mut()
        .unwrap()
        .extend(fields.as_object().unwrap().clone());
    PublicTurn::read(&json!({ "turn": turn }))
}

#[test]
fn a_turn_answers_with_its_final_answer_over_a_hook_reason() {
    let answered = turn(json!({"finalAnswer": "The final answer."}));
    assert_eq!(answered.answer(), Some("The final answer."));
    let skipped = turn(json!({"skipReason": "Deploys are frozen"}));
    assert_eq!(skipped.answer(), Some("Deploys are frozen"));
    let both = turn(json!({"finalAnswer": "The final answer.", "rejectReason": "no"}));
    assert_eq!(both.answer(), Some("The final answer."));
}

#[test]
fn a_turn_from_a_server_without_final_answer_has_no_answer() {
    let older = turn(json!({}));
    assert_eq!(older.final_answer, None);
    assert_eq!(older.answer(), None);
    let null = turn(json!({"finalAnswer": null}));
    assert_eq!(null.answer(), None);
}

#[test]
fn a_text_run_prints_the_final_answer_not_a_draft_the_history_still_shows() {
    let history = json!([
        {
            "type": "message",
            "role": "assistant",
            "turnId": "t1",
            "content": [{"type": "text", "text": "The final answer."}]
        },
        {
            "type": "message",
            "role": "assistant",
            "turnId": "t1",
            "content": [{"type": "text", "text": "A late draft."}]
        }
    ]);
    let output = Output::new(OutputFormat::Silent);
    let answered = turn(json!({"finalAnswer": "The final answer."}));
    let printed = output
        .finalize(history, Some("t1"), answered.answer())
        .unwrap();
    assert_eq!(printed.as_deref(), Some("The final answer."));
}

#[test]
fn a_text_run_without_a_final_answer_falls_back_to_the_history() {
    let history = json!([
        {
            "type": "message",
            "role": "assistant",
            "turnId": "t1",
            "content": [{"type": "text", "text": "From the history."}]
        }
    ]);
    let output = Output::new(OutputFormat::Silent);
    let printed = output
        .finalize(history, Some("t1"), turn(json!({})).answer())
        .unwrap();
    assert_eq!(printed.as_deref(), Some("From the history."));
}
