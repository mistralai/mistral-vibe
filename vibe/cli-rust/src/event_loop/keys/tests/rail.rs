use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::json;
use tokio::sync::mpsc;

use super::super::dispatch_key;
use crate::app::App;
use crate::focus::Focus;
use crate::rail::{self, Item};
use crate::server::{Client, PublicChildSession, SessionStatus};

#[test]
fn ctrl_c_over_the_letters_closes_the_window_back_to_the_rail() {
    let mut app = App::default();
    app.goal.goal = Some(
        serde_json::from_value(json!({
            "goalId": "goal-1", "revision": 1, "objectiveRevision": 1,
            "objective": "Ship it", "status": "active", "tokenBudget": 50000,
            "tokensUsed": 12500, "timeUsedSeconds": 240,
        }))
        .unwrap(),
    );
    rail::open(&mut app, &Arc::new(Client::stub()), Item::Goal);
    rail::enter(&mut app);
    let (config_tx, _config_rx) = mpsc::channel(1);
    let ctrl_c = KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL);
    dispatch_key(
        &mut app,
        &Arc::new(Client::stub()),
        &config_tx,
        ctrl_c,
        None,
    );

    assert_eq!(rail::window(&app), None);
    assert_eq!(app.focus(), Focus::Rail);
    assert!(!app.rail.letters);
}

#[tokio::test]
async fn a_child_view_opened_from_the_rail_ignores_rail_keys_and_esc_lands_on_the_prompt() {
    let mut app = App::default();
    app.goal.goal = Some(
        serde_json::from_value(json!({
            "goalId": "goal-1", "revision": 1, "objectiveRevision": 1,
            "objective": "Ship it", "status": "active", "tokenBudget": 50000,
            "tokensUsed": 12500, "timeUsedSeconds": 240,
        }))
        .unwrap(),
    );
    app.subagents.replace_child_session(PublicChildSession {
        id: "child".into(),
        name: "explore-1".into(),
        agent_type: "explore".into(),
        status: SessionStatus::Running,
        created_at: 1,
        updated_at: 1,
        ..Default::default()
    });
    crate::subagents::refresh(&mut app);
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel(1);
    let press = |app: &mut App, code: KeyCode, modifiers: KeyModifiers| {
        dispatch_key(
            app,
            &client,
            &config_tx,
            KeyEvent::new(code, modifiers),
            None,
        );
    };
    press(&mut app, KeyCode::Char('x'), KeyModifiers::CONTROL);
    press(&mut app, KeyCode::Char('s'), KeyModifiers::NONE);
    press(&mut app, KeyCode::Down, KeyModifiers::NONE);
    press(&mut app, KeyCode::Enter, KeyModifiers::NONE);
    assert_eq!(app.subagents.viewed_subagent_id.as_deref(), Some("child"));

    for code in [
        KeyCode::Right,
        KeyCode::Left,
        KeyCode::Tab,
        KeyCode::BackTab,
    ] {
        press(&mut app, code, KeyModifiers::NONE);
        assert_eq!(app.subagents.viewed_subagent_id.as_deref(), Some("child"));
        assert_eq!(rail::window(&app), None);
    }

    press(&mut app, KeyCode::Esc, KeyModifiers::NONE);
    assert_eq!(app.subagents.viewed_subagent_id, None);
    assert_eq!(app.focus(), Focus::Composer);
    press(&mut app, KeyCode::Char('g'), KeyModifiers::NONE);
    assert_eq!(app.chat_input.input, "g");
    assert_eq!(rail::window(&app), None);
}

#[tokio::test]
async fn a_rail_whose_last_item_left_hands_keys_back_without_esc_interrupting() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel(1);
    let press = |app: &mut App, code: KeyCode, modifiers: KeyModifiers| {
        dispatch_key(
            app,
            &client,
            &config_tx,
            KeyEvent::new(code, modifiers),
            None,
        );
    };
    let tasks_window = || {
        let mut app = App::default();
        app.session.status = crate::app::Status::Generating {
            since: crate::clock::now(),
        };
        app.session.background_tasks = vec![serde_json::from_value(json!({
            "processId": "watch", "command": "watch", "status": "running",
        }))
        .unwrap()];
        press(&mut app, KeyCode::Char('x'), KeyModifiers::CONTROL);
        press(&mut app, KeyCode::Char('b'), KeyModifiers::NONE);
        assert_eq!(rail::window(&app), Some(Item::Tasks));
        app.session.background_tasks.clear();
        app
    };

    let mut app = tasks_window();
    for character in "hello".chars() {
        press(&mut app, KeyCode::Char(character), KeyModifiers::NONE);
    }
    assert_eq!(app.chat_input.input, "hello");

    let mut app = tasks_window();
    press(&mut app, KeyCode::Esc, KeyModifiers::NONE);
    assert_eq!(app.focus(), Focus::Composer);
    assert!(matches!(
        app.session.status,
        crate::app::Status::Generating { .. }
    ));

    let mut app = tasks_window();
    rail::prune(&mut app);
    assert_eq!(app.focus(), Focus::Composer);
}
