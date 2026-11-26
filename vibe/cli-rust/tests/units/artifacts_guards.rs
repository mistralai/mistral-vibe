//! The PR list keeps the composer draft and stays behind its config flag.

use crossterm::event::KeyCode;
use serde_json::json;

use vibe_rs::artifacts_modal;
use vibe_rs::commands::{builtin, entries};
use vibe_rs::input::handle_paste;
use vibe_rs::input_modes::{classify, ClassifiedInput, InputMode};
use vibe_rs::rail::{self, Item};
use vibe_rs::skill::Skill;

use crate::artifacts_actions::{app_with, blocked, pr};
use crate::support::{press, stub};

const FIX: &str = "Resolve the merge conflicts on PR #7 (https://github.com/o/r/pull/7).";

#[tokio::test]
async fn f_keeps_a_collapsed_paste_in_the_draft() {
    let mut app = app_with(vec![blocked(json!({ "prStatusReason": "conflicts" }))]);
    let paste = "log line\n".repeat(40);
    handle_paste(&mut app, paste.clone());
    artifacts_modal::open(&mut app, &stub());
    press(&mut app, KeyCode::Char('f'));
    assert_eq!(app.chat_input.submitted_text(), format!("{paste}\n\n{FIX}"));
}

#[tokio::test]
async fn f_leaves_a_command_draft_alone() {
    let mut app = app_with(vec![blocked(json!({ "prStatusReason": "conflicts" }))]);
    app.chat_input.load_full_text("!git status".into());
    artifacts_modal::open(&mut app, &stub());
    press(&mut app, KeyCode::Char('f'));
    assert_eq!(app.chat_input.mode, InputMode::Bash);
    assert_eq!(app.chat_input.full_text(), "!git status");
}

#[test]
fn pr_is_offered_only_with_pr_monitoring() {
    let offers = |on: bool| {
        entries(&[], |_| on)
            .iter()
            .any(|entry| entry.label == "/pr")
    };
    assert!(offers(true));
    assert!(!offers(false));
    assert!(!builtin(|_| false).iter().any(|(label, _)| label == "`/pr`"));
}

#[test]
fn the_pr_built_in_wins_over_a_pr_skill() {
    let skills = [Skill::new("pr", "my skill")];
    for on in [false, true] {
        assert!(!entries(&skills, |_| on)
            .iter()
            .any(|entry| entry.origin.is_some()));
    }
    assert_eq!(
        classify("/pr", &skills),
        ClassifiedInput::SlashCommand { command: "/pr" }
    );
}

#[test]
fn the_rail_chip_is_hidden_while_pr_monitoring_is_off() {
    let mut app = app_with(vec![pr(1, 10, json!({ "prStatus": "mergeable" }))]);
    assert!(rail::items(&app).contains(&Item::Prs));
    app.artifacts_modal.enabled = false;
    assert!(!rail::items(&app).contains(&Item::Prs));
}

#[test]
fn enter_runs_a_typed_pr_that_completion_hides() {
    let mut app = vibe_rs::app::App::default();
    app.chat_input.mode = InputMode::Slash;
    app.chat_input.input = "pr".into();
    app.chat_input.cursor = 2;
    vibe_rs::completion_manager::input_changed(&mut app);
    assert!(
        !app.completion.entries.is_empty(),
        "a fuzzy match is offered"
    );
    assert!(!vibe_rs::completion_manager::accept_on_enter(&mut app));
    assert_eq!(app.chat_input.input, "pr");
}
