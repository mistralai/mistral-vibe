//! The PR list keeps the composer draft, waits for panels over it, and stays behind its config flag.

use crossterm::event::KeyCode;
use serde_json::json;

use vibe_rs::artifacts_modal;
use vibe_rs::commands::{builtin, entries};
use vibe_rs::input::{handle_artifacts_modal_key, handle_paste};
use vibe_rs::input_modes::{classify, ClassifiedInput, InputMode};
use vibe_rs::skill::Skill;
use vibe_rs::ui::bottom_bar::BarLinkAction;

use crate::artifacts_actions::{app_with, blocked, pr};
use crate::support::{click, key, render};

const FIX: &str = "Resolve the merge conflicts on PR #7 (https://github.com/o/r/pull/7).";

#[test]
fn f_keeps_a_collapsed_paste_in_the_draft() {
    let mut app = app_with(vec![blocked(json!({ "prStatusReason": "conflicts" }))]);
    let paste = "log line\n".repeat(40);
    handle_paste(&mut app, paste.clone());
    artifacts_modal::open(&mut app);
    handle_artifacts_modal_key(&mut app, key(KeyCode::Char('f')));
    assert_eq!(app.chat_input.submitted_text(), format!("{paste}\n\n{FIX}"));
}

#[test]
fn f_leaves_a_command_draft_alone() {
    let mut app = app_with(vec![blocked(json!({ "prStatusReason": "conflicts" }))]);
    app.chat_input.load_full_text("!git status".into());
    artifacts_modal::open(&mut app);
    handle_artifacts_modal_key(&mut app, key(KeyCode::Char('f')));
    assert_eq!(app.chat_input.mode, InputMode::Bash);
    assert_eq!(app.chat_input.full_text(), "!git status");
}

#[tokio::test]
async fn the_footer_link_does_nothing_under_another_panel() {
    let mut app = app_with(vec![pr(1, 10, json!({ "prStatus": "mergeable" }))]);
    app.model_picker.open = true;
    render(100, 30, |f| app.draw(f));
    let link = app.view.bottom_bar_links.last().expect("PR link").area;
    click(&mut app, link.x, link.y);
    assert!(!app.artifacts_modal.open);
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

#[tokio::test]
async fn the_footer_link_is_hidden_while_pr_monitoring_is_off() {
    let mut app = app_with(vec![pr(1, 10, json!({ "prStatus": "mergeable" }))]);
    app.artifacts_modal.enabled = false;
    render(100, 30, |f| app.draw(f));
    assert!(!app
        .view
        .bottom_bar_links
        .iter()
        .any(|link| matches!(link.action, BarLinkAction::ListArtifacts)));
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
