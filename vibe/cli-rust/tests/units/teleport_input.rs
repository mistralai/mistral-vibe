//! `&` input, teleport event parsing, and the push question.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::app::{App, ChatInput};
use vibe_rs::input_modes::{classify, ClassifiedInput, InputMode};
use vibe_rs::server::proto_teleport::{parse_event, TeleportError, TeleportEvent};
use vibe_rs::teleport;

use crate::teleport_support::{event, stub};

// Intentional divergence: Python keeps `value[1:]` verbatim, Rust trims the target.
#[test]
fn ampersand_input_is_a_teleport_with_a_trimmed_prompt() {
    assert_eq!(
        classify("&  fix the tests ", &[]),
        ClassifiedInput::Teleport {
            target: "fix the tests".into()
        }
    );
    assert_eq!(
        classify("&", &[]),
        ClassifiedInput::Teleport {
            target: String::new()
        }
    );
    let mut input = ChatInput::default();
    input.load_full_text("&deploy".into());
    assert_eq!(input.mode, InputMode::Teleport);
    assert_eq!(input.input, "deploy");
    assert_eq!(input.full_text(), "&deploy");
    assert_eq!(InputMode::Teleport.marker(), "& ");
}

#[test]
fn typing_ampersand_on_an_empty_prompt_enters_teleport_mode() {
    let mut app = App::default();
    let (tx, _rx) = tokio::sync::mpsc::channel(1);
    vibe_rs::input::handle_key(&mut app, &stub(), &tx, KeyCode::Char('&').into());
    assert_eq!(app.chat_input.mode, InputMode::Teleport);
    assert!(app.chat_input.input.is_empty());
}

#[test]
fn every_event_kind_parses_with_its_operation_id() {
    let cases = [
        (
            json!({"kind": "summarizing_context"}),
            TeleportEvent::SummarizingContext,
        ),
        (json!({"kind": "checking_git"}), TeleportEvent::CheckingGit),
        (
            json!({"kind": "push_required", "unpushedCount": 3, "branchNotPushed": true}),
            TeleportEvent::PushRequired {
                unpushed_count: 3,
                branch_not_pushed: true,
            },
        ),
        (json!({"kind": "pushing"}), TeleportEvent::Pushing),
        (
            json!({"kind": "starting_workflow"}),
            TeleportEvent::StartingWorkflow,
        ),
        (
            json!({"kind": "complete", "url": "https://chat.example/code/1"}),
            TeleportEvent::Complete {
                url: "https://chat.example/code/1".into(),
            },
        ),
        (
            json!({"kind": "failed", "error": {"message": "boom", "code": "teleport_failed"}}),
            TeleportEvent::Failed {
                error: TeleportError {
                    message: "boom".into(),
                    code: Some("teleport_failed".into()),
                },
            },
        ),
    ];
    for (kind, expected) in cases {
        assert_eq!(
            parse_event(&event("op", kind)),
            Some(("op".into(), expected))
        );
    }
    assert_eq!(parse_event(&json!({"event": {"kind": "complete"}})), None);
}

#[test]
fn push_question_matches_python_wording() {
    let single = teleport::push_question(1, false);
    let question = &single.questions[0];
    assert_eq!(
        question.question,
        "You have 1 unpushed commit. Push to continue?"
    );
    assert_eq!(question.header, "Push");
    assert!(question.hide_other);
    let labels: Vec<_> = question.options.iter().map(|o| o.label.as_str()).collect();
    assert_eq!(labels, [teleport::PUSH_LABEL, "Cancel"]);
    assert_eq!(
        teleport::push_question(4, false).questions[0].question,
        "You have 4 unpushed commits. Push to continue?"
    );
    assert_eq!(
        teleport::push_question(0, true).questions[0].question,
        "Your branch doesn't exist on remote. Push to continue?"
    );
}
