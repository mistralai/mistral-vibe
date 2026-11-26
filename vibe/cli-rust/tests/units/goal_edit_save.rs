//! Saving `/goal edit`: an empty objective stays in the form, and a rejected save reopens it.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::goal::{self, EditForm, Panel};
use vibe_rs::server::{Client, PublicGoal};
use vibe_rs::ui_event::{self, UiEvent};

use crate::support::key;

fn goal(objective: &str) -> PublicGoal {
    serde_json::from_value(json!({
        "goalId": "goal-1", "revision": 3, "objective": objective, "status": "active",
        "tokenBudget": 50000, "tokensUsed": 12500, "timeUsedSeconds": 240,
    }))
    .unwrap()
}

fn editing(app: &mut App, client: &Arc<Client>, typed: &str) {
    app.set_session_id("s".into());
    app.goal.goal = Some(goal("Ship it"));
    goal::command(app, client, "edit");
    goal::paste(app, typed);
}

fn form(app: &App) -> &EditForm {
    match &app.goal.panel {
        Some(Panel::Edit { form, .. }) => form,
        _ => panic!("the edit form is open"),
    }
}

#[tokio::test]
async fn an_empty_objective_is_refused_in_the_form() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let mut app = App::default();
    editing(&mut app, &client, "");
    if let Some(Panel::Edit { form, .. }) = app.goal.panel.as_mut() {
        form.objective = " \n ".into();
        form.cursor = form.objective.len();
    }
    goal::handle_key(&mut app, &client, key(KeyCode::Enter));
    assert!(form(&app).error.is_some(), "the form shows why");
    let sent = tokio::time::timeout(Duration::from_millis(50), frames.recv()).await;
    assert!(sent.is_err(), "nothing is sent");
}

/// Type into the form, save it against a server that rejects it, and apply the answer after `meanwhile`.
async fn rejected_save(meanwhile: impl FnOnce(&mut App)) -> App {
    let client = Arc::new(Client::stub());
    let (tx, mut rx) = mpsc::channel::<UiEvent>(8);
    let mut app = App {
        events: Some(tx),
        ..App::default()
    };
    editing(&mut app, &client, " now");
    goal::handle_key(&mut app, &client, key(KeyCode::Tab));
    goal::paste(&mut app, "200k");
    goal::handle_key(&mut app, &client, key(KeyCode::Enter));
    assert!(
        app.goal.panel.is_none(),
        "the form closes while the save runs"
    );
    meanwhile(&mut app);
    let event = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("the save answers")
        .expect("an event");
    ui_event::apply(&mut app, &client, event);
    assert!(app.overlays.notice.is_some(), "the failure is shown");
    app
}

#[tokio::test]
async fn a_rejected_save_reopens_the_form_with_what_was_typed() {
    let app = rejected_save(|_| {}).await;
    let form = form(&app);
    assert_eq!(form.objective, "Ship it now");
    assert_eq!(form.budget.text, "200k");
}

#[tokio::test]
async fn a_conflict_reopens_the_form_on_the_goal_revision_it_lost_to() {
    let app = rejected_save(|app| {
        if let Some(goal) = app.goal.goal.as_mut() {
            goal.revision = 4;
        }
    })
    .await;
    assert_eq!(form(&app).revision, 4);
    assert_eq!(form(&app).objective, "Ship it now");
}

#[tokio::test]
async fn a_rejected_save_stays_closed_when_the_goal_was_cleared() {
    let app = rejected_save(|app| app.goal.goal = None).await;
    assert!(app.goal.panel.is_none());
}

#[tokio::test]
async fn a_rejected_save_stays_closed_when_the_goal_was_replaced() {
    let app = rejected_save(|app| {
        if let Some(goal) = app.goal.goal.as_mut() {
            goal.goal_id = "goal-2".into();
        }
    })
    .await;
    assert!(app.goal.panel.is_none());
}
