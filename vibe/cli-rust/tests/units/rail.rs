//! Status rail items, arrows, and what Enter opens.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::focus::Focus;
use vibe_rs::rail::{self, Item};
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::todo_tracker::{TodoItem, TodoStatus};

use crate::support::{ctrl, key, press, stub};

pub(crate) fn with_tasks(app: &mut App, commands: &[&str]) {
    app.session.background_tasks = commands
        .iter()
        .map(|command| {
            serde_json::from_value(json!({
                "processId": command, "command": command, "status": "running",
            }))
            .unwrap()
        })
        .collect();
}

pub(crate) fn with_items(running_child: bool) -> App {
    let mut app = App::default();
    app.goal.goal = Some(
        serde_json::from_value(json!({
            "goalId": "goal-1", "revision": 1, "objectiveRevision": 1,
            "objective": "Ship it", "status": "active", "tokenBudget": 50000,
            "tokensUsed": 12500, "timeUsedSeconds": 240,
        }))
        .unwrap(),
    );
    app.todo_tracker.record(vec![TodoItem {
        id: "1".into(),
        content: "Run the suite".into(),
        status: TodoStatus::InProgress,
    }]);
    if running_child {
        app.subagents.replace_child_session(PublicChildSession {
            id: "a".into(),
            name: "explore-1".into(),
            agent_type: "explore".into(),
            status: SessionStatus::Running,
            ..Default::default()
        });
        vibe_rs::subagents::refresh(&mut app);
    }
    app
}

#[test]
fn items_keep_their_order_with_the_plan_last() {
    let mut app = with_items(true);
    with_tasks(&mut app, &["a", "b"]);

    assert_eq!(
        rail::items(&app),
        [Item::Agents, Item::Tasks, Item::Goal, Item::Plan]
    );
}

#[test]
fn arrows_wrap_through_every_item() {
    let mut app = with_items(true);
    with_tasks(&mut app, &["kubectl get jobs -w"]);
    assert!(rail::enter(&mut app));
    assert_eq!(rail::selected(&app), Some(Item::Agents));

    press(&mut app, KeyCode::Right);
    assert_eq!(rail::selected(&app), Some(Item::Tasks));
    for _ in 0..3 {
        press(&mut app, KeyCode::Right);
    }
    assert_eq!(
        rail::selected(&app),
        Some(Item::Agents),
        "Right wraps to the first item"
    );
    press(&mut app, KeyCode::Left);
    assert_eq!(
        rail::selected(&app),
        Some(Item::Plan),
        "Left wraps to the last item"
    );
}

#[test]
fn b_opens_the_tasks_panel_and_the_last_stop_closes_it_back_to_the_rail() {
    let mut app = with_items(false);
    with_tasks(&mut app, &["kubectl get jobs -w", "tail -f capture.log"]);
    rail::enter(&mut app);
    press(&mut app, KeyCode::Char('b'));
    assert_eq!(app.focus(), Focus::RailWindow);

    let client = std::sync::Arc::new(vibe_rs::server::Client::stub());
    vibe_rs::tasks_panel::handle_key(&mut app, &client, key(KeyCode::Enter));
    assert_eq!(
        app.session.background_tasks.len(),
        2,
        "Enter never stops a task"
    );
    vibe_rs::tasks_panel::handle_key(&mut app, &client, ctrl('j'));
    let stop = key(KeyCode::Char('s'));
    vibe_rs::tasks_panel::handle_key(&mut app, &client, stop);
    assert_eq!(app.session.background_tasks.len(), 1);
    assert_eq!(
        app.session.background_tasks[0].command,
        "tail -f capture.log"
    );
    vibe_rs::tasks_panel::handle_key(&mut app, &client, stop);
    assert!(app.session.background_tasks.is_empty());
    assert_eq!(app.focus(), Focus::Rail);
}

#[test]
fn a_panel_opened_from_the_rail_closes_back_to_it() {
    let mut app = with_items(true);
    rail::enter(&mut app);
    press(&mut app, KeyCode::Enter);
    assert_eq!(app.focus(), Focus::SubagentList);

    vibe_rs::subagents::leave_list(&mut app);
    assert_eq!(app.focus(), Focus::Rail);

    press(&mut app, KeyCode::Right);
    press(&mut app, KeyCode::Enter);
    assert_eq!(app.focus(), Focus::RailWindow);
    assert_eq!(rail::window(&app), Some(Item::Goal));
    press(&mut app, KeyCode::Esc);
    assert_eq!(app.focus(), Focus::Rail);
}

#[test]
fn the_goal_window_edit_opens_over_it_and_closes_back_to_it() {
    let mut app = with_items(false);
    rail::open(&mut app, &stub(), Item::Goal);
    press(&mut app, KeyCode::Char('e'));
    assert_eq!(app.focus(), Focus::Goal);

    vibe_rs::goal::handle_key(&mut app, &stub(), key(KeyCode::Esc));
    assert_eq!(app.focus(), Focus::RailWindow);
}

#[test]
fn a_window_closes_when_its_item_leaves_the_rail() {
    let mut app = with_items(false);
    rail::open(&mut app, &stub(), Item::Plan);
    assert_eq!(app.focus(), Focus::RailWindow);

    app.todo_tracker.clear();
    assert_eq!(rail::window(&app), None);
    assert_ne!(app.focus(), Focus::RailWindow);
}

#[test]
fn enter_on_the_plan_opens_its_window_and_esc_returns_to_the_rail() {
    let mut app = with_items(false);
    rail::enter(&mut app);
    press(&mut app, KeyCode::Left);
    press(&mut app, KeyCode::Enter);
    assert_eq!(rail::window(&app), Some(Item::Plan));

    press(&mut app, KeyCode::Esc);
    assert_eq!(app.focus(), Focus::Rail);
}

#[test]
fn esc_or_a_typed_key_hands_focus_back_to_the_prompt() {
    let mut app = with_items(false);
    rail::enter(&mut app);
    press(&mut app, KeyCode::Esc);
    assert_eq!(app.focus(), Focus::Composer);
    assert!(
        app.overlays.last_escape.is_none(),
        "the rail's Esc never arms the prompt's double Esc"
    );

    rail::enter(&mut app);
    press(&mut app, KeyCode::Char('x'));
    assert_eq!(app.focus(), Focus::Composer);
    assert_eq!(
        app.chat_input.input, "x",
        "the key falls through to the prompt"
    );
}

#[test]
fn a_narrow_row_shrinks_chips_from_the_left_before_dropping_any() {
    let mut app = with_items(true);
    let full = crate::support::row(&crate::support::rail(&mut app, 80), 0);
    assert!(full.contains("◐ 1 agent · ▶ goal 4m · ≡ 0/1"), "{full:?}");

    let first_short = crate::support::row(&crate::support::rail(&mut app, 26), 0);
    assert!(
        first_short.contains("◐ 1 · ▶ goal 4m · ≡ 0/1"),
        "{first_short:?}"
    );
    let all_short = crate::support::row(&crate::support::rail(&mut app, 21), 0);
    assert!(all_short.contains("◐ 1 · ▶ 4m · ≡ 0/1"), "{all_short:?}");

    let plan_only = vibe_rs::ui::rail::width(&app, 8);
    assert_eq!(plan_only, "  ≡ 0/1 ".chars().count() as u16);
}

#[test]
fn focusing_the_rail_moves_no_chip() {
    let mut app = with_items(true);
    with_tasks(&mut app, &["a", "b"]);
    // The rail is right-aligned, so a chip's place is its offset from the rail's right edge.
    let offsets = |app: &mut App| {
        let width = vibe_rs::ui::rail::width(app, u16::MAX);
        crate::support::rail(app, width);
        let hits = app.rail.hits.clone();
        hits.iter().map(|(_, x, _)| width - x).collect::<Vec<_>>()
    };
    let before = offsets(&mut app);

    rail::enter(&mut app);
    assert_eq!(
        offsets(&mut app),
        before,
        "the hint goes, the letters take the glyphs' place"
    );
}
