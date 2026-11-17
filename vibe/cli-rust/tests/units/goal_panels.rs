//! `/goal` panels in short terminals, stale-goal guards, and the resume prompt's digits.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::feedback::Message;
use vibe_rs::goal::{self, EditForm, Panel};
use vibe_rs::server::{Client, PublicGoal};
use vibe_rs::vim::{Mode, Vim};

use crate::support::{key, press, render, send_key};

fn goal_with(goal_id: &str, objective: &str) -> PublicGoal {
    serde_json::from_value(json!({
        "goalId": goal_id, "revision": 1, "objectiveRevision": 1,
        "objective": objective, "status": "active", "tokenBudget": 50000,
        "tokensUsed": 12500, "timeUsedSeconds": 240,
    }))
    .unwrap()
}

fn long_goal() -> PublicGoal {
    goal_with(
        "goal-1",
        &"Make the parity suite pass on every platform. ".repeat(12),
    )
}

fn panels() -> Vec<Panel> {
    let confirm = |app: &mut App| {
        goal::command(app, &Arc::new(Client::stub()), "clear");
        app.goal.panel.take().expect("the clear confirmation")
    };
    let mut app = App::default();
    app.goal.goal = Some(long_goal());
    vec![
        Panel::Detail,
        Panel::Edit {
            form: Box::new(EditForm::new(&long_goal())),
            nested: false,
        },
        confirm(&mut app),
    ]
}

#[test]
fn goal_panels_render_in_short_and_narrow_terminals() {
    for width in [12, 40, 80] {
        for height in 1..=30 {
            for panel in panels() {
                let mut app = App::default();
                app.goal.goal = Some(long_goal());
                app.goal.panel = Some(panel);
                render(width, height, |frame| app.draw(frame));
            }
        }
    }
}

async fn next_request(frames: &mut mpsc::UnboundedReceiver<Value>) -> Value {
    tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("a request is sent")
        .expect("the stub answers")
}

fn session_app() -> App {
    let mut app = App::default();
    app.set_session_id("s".to_owned());
    app.goal.goal = Some(goal_with("goal-a", "A"));
    app
}

#[tokio::test]
async fn an_edit_saves_only_onto_the_goal_it_opened_on() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let mut app = session_app();
    goal::command(&mut app, &client, "edit");
    app.goal.goal = Some(goal_with("goal-b", "B"));
    goal::handle_key(&mut app, &client, key(KeyCode::Enter));
    let request = next_request(&mut frames).await;
    assert_eq!(request["method"], "goal/set");
    assert_eq!(request["params"]["expectedGoalId"], "goal-a");
    assert_eq!(request["params"]["expectedRevision"], 1);
}

#[tokio::test]
async fn a_clear_confirmation_clears_only_the_goal_it_opened_on() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let mut app = session_app();
    goal::command(&mut app, &client, "clear");
    app.goal.goal = Some(goal_with("goal-b", "B"));
    goal::handle_key(&mut app, &client, key(KeyCode::Enter));
    let request = next_request(&mut frames).await;
    assert_eq!(request["method"], "goal/clear");
    assert_eq!(request["params"]["expectedGoalId"], "goal-a");
}

fn suspended_app() -> App {
    let mut app = App::default();
    let mut suspended = goal_with("goal-1", "Ship it");
    suspended.status = vibe_rs::server::GoalStatus::Paused;
    suspended.status_reason = Some("suspended".into());
    goal::adopt(&mut app, Some(Box::new(suspended)), true);
    assert!(app.goal.resume_prompt);
    app
}

#[test]
fn digits_answer_the_resume_prompt_in_vim_normal_mode() {
    let mut app = suspended_app();
    app.vim = Vim {
        enabled: true,
        mode: Mode::Normal,
        ..Vim::default()
    };
    send_key(&mut app, key(KeyCode::Char('2')));
    assert!(!app.goal.resume_prompt);
}

#[test]
fn a_digit_rates_the_turn_before_it_answers_the_resume_prompt() {
    let mut app = suspended_app();
    app.feedback.message = Message::Prompt;
    press(&mut app, KeyCode::Char('1'));
    assert!(app.feedback.message == Message::ThankYou);
    assert!(app.goal.resume_prompt, "the goal stays paused");
}

#[tokio::test]
async fn shift_tab_in_the_edit_form_switches_the_field_not_the_agent() {
    let client = Arc::new(Client::stub());
    let mut app = session_app();
    let agents: Vec<Value> = ["default", "plan"]
        .iter()
        .map(|name| json!({"name": name, "displayName": name, "agentType": "agent"}))
        .collect();
    let runtime = json!({"runtime": {"activeAgent": {"name": "default"}, "agents": agents}});
    vibe_rs::agents::apply_runtime(&mut app, &runtime);
    goal::command(&mut app, &client, "edit");
    let shift_tab = key(KeyCode::BackTab);
    if vibe_rs::input::handle_priority_key(&mut app, &client, shift_tab).is_none() {
        goal::handle_key(&mut app, &client, shift_tab);
    }
    assert_eq!(app.agents.desired, None);
    let Some(Panel::Edit { form, .. }) = &app.goal.panel else {
        panic!("the edit form stays open");
    };
    assert!(form.budget_focused);
}
