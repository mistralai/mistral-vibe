//! The `/loop` add form's instructions: multi-line with Ctrl+J or Shift+Enter, wrapped and scrolled like the question's free-text answer.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::loops::{self, FormField};
use vibe_rs::server::{notification, Client};
use vibe_rs::ui;

use crate::support::{ctrl, key, notify, render, rows};

fn form() -> (App, Arc<Client>) {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    app.set_status(Status::Ready);
    notify(
        &mut app,
        notification::SESSION_SNAPSHOT,
        json!({"sessionId": "session", "state": {
            "eventId": 1, "session": {"id": "session", "loops": []}, "history": [],
        }}),
    );
    loops::open(&mut app);
    loops::handle_key(&mut app, &client, key(KeyCode::Char('n')));
    (app, client)
}

fn screen(app: &mut App, width: u16) -> String {
    rows(&render(width, 30, |frame| ui::draw(app, frame))).join("\n")
}

fn type_text(app: &mut App, client: &Arc<Client>, text: &str) {
    for c in text.chars() {
        loops::handle_key(app, client, key(KeyCode::Char(c)));
    }
}

fn to_instructions(app: &mut App, client: &Arc<Client>) {
    for _ in 0..2 {
        loops::handle_key(app, client, key(KeyCode::Tab));
    }
}

fn instructions(app: &App) -> &str {
    &app.loops.form.as_ref().unwrap().instructions.text
}

#[test]
fn ctrl_j_and_shift_enter_break_the_instructions_but_not_the_title() {
    let (mut app, client) = form();
    loops::handle_key(&mut app, &client, ctrl('j'));
    assert_eq!(app.loops.form.as_ref().unwrap().title.text, "");
    to_instructions(&mut app, &client);
    type_text(&mut app, &client, "one");
    loops::handle_key(&mut app, &client, ctrl('j'));
    type_text(&mut app, &client, "two");
    let shift_enter = KeyEvent::new(KeyCode::Enter, KeyModifiers::SHIFT);
    loops::handle_key(&mut app, &client, shift_enter);
    type_text(&mut app, &client, "three");
    assert_eq!(instructions(&app), "one\ntwo\nthree");
    assert!(app.loops.form.is_some(), "a line break does not submit");
}

#[test]
fn the_instructions_grow_up_to_five_rows_then_scroll() {
    let (mut app, client) = form();
    to_instructions(&mut app, &client);
    for n in 1..=3 {
        type_text(&mut app, &client, &format!("line {n}"));
        loops::handle_key(&mut app, &client, ctrl('j'));
    }
    let three = screen(&mut app, 80);
    let lines: Vec<&str> = three.lines().collect();
    let at = lines
        .iter()
        .position(|row| row.contains("line 1"))
        .expect("the field");
    assert!(lines[at].contains("Instructions"), "{three}");
    assert!(
        lines[at + 1].contains("line 2") && lines[at + 2].contains("line 3"),
        "{three}"
    );
    for n in 4..=8 {
        type_text(&mut app, &client, &format!("line {n}"));
        loops::handle_key(&mut app, &client, ctrl('j'));
    }
    type_text(&mut app, &client, "last");
    let full = screen(&mut app, 80);
    let shown = (1..=8)
        .filter(|n| full.contains(&format!("line {n} ")))
        .count()
        + usize::from(full.contains("last"));
    assert_eq!(shown, 5, "{full}");
    assert!(full.contains("last") && full.contains('↑'), "{full}");
}

#[test]
fn up_and_down_move_through_the_instructions_rows_before_leaving_the_field() {
    let (mut app, client) = form();
    to_instructions(&mut app, &client);
    type_text(&mut app, &client, "one");
    loops::handle_key(&mut app, &client, ctrl('j'));
    type_text(&mut app, &client, "two");
    let _ = screen(&mut app, 80);
    let focused = |app: &App| app.loops.form.as_ref().unwrap().focused_field();
    loops::handle_key(&mut app, &client, key(KeyCode::Up));
    assert_eq!(focused(&app), FormField::Instructions);
    type_text(&mut app, &client, "!");
    assert_eq!(instructions(&app), "one!\ntwo");
    loops::handle_key(&mut app, &client, key(KeyCode::Up));
    assert_eq!(
        focused(&app),
        FormField::Schedule,
        "the first row leaves upward"
    );
    loops::handle_key(&mut app, &client, key(KeyCode::Down));
    assert_eq!(focused(&app), FormField::Instructions);
}

#[test]
fn ctrl_j_is_hinted_only_on_the_instructions() {
    let (mut app, client) = form();
    let title = screen(&mut app, 120);
    assert!(!title.contains("Ctrl+J"), "{title}");
    to_instructions(&mut app, &client);
    let instructions = screen(&mut app, 120);
    assert!(
        instructions.contains("↑↓/Tab navigate  Enter create  Ctrl+J newline  Esc back"),
        "{instructions}"
    );
}

#[test]
fn a_scroll_arrow_never_covers_the_caret() {
    let (mut app, client) = form();
    to_instructions(&mut app, &client);
    let width = 60;
    let _ = screen(&mut app, width);
    type_text(&mut app, &client, "x");
    let _ = screen(&mut app, width);
    let full = usize::from(app.loops.form.as_ref().unwrap().width) - 1;
    let line = "y".repeat(full);
    for _ in 0..8 {
        loops::handle_key(&mut app, &client, ctrl('j'));
        type_text(&mut app, &client, &line);
    }
    for _ in 0..3 {
        loops::handle_key(&mut app, &client, key(KeyCode::Up));
    }
    let screen = screen(&mut app, width);
    let rows: Vec<&str> = screen.lines().collect();
    let last = rows
        .iter()
        .rposition(|row| row.contains(&line))
        .expect("a full row");
    assert!(rows[last].contains('↓'), "lines hidden below\n{screen}");
    let after = rows[last].split_once(&line).unwrap().1;
    assert!(
        after.starts_with(' '),
        "the caret cell stays free of the arrow\n{screen}"
    );
}
