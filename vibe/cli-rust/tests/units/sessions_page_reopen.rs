//! The page keeps its listing and place across opens, and refreshes under them.

use crossterm::event::{KeyCode, KeyEvent};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::server::PublicSessionState;
use vibe_rs::sessions_page::actions::{self, Event};
use vibe_rs::sessions_page::row::Session;
use vibe_rs::sessions_page::{self, keys, Trigger};
use vibe_rs::ui_event::UiEvent;

use crate::support::stub;

fn session(id: &str, activity: u64) -> Session {
    Session {
        id: id.into(),
        title: id.into(),
        cwd: Some("/work/app".into()),
        repository_root: Some("/work/app".into()),
        created_at: activity,
        updated_at: activity,
        bumped_at: Some(activity),
        ..Default::default()
    }
}

/// A ready app whose page listed `sessions`; the receiver keeps its requests' answers.
fn listed(sessions: Vec<Session>) -> (App, mpsc::Receiver<UiEvent>) {
    let mut app = App::default();
    app.set_status(Status::Ready);
    let (tx, rx) = mpsc::channel(64);
    app.events = Some(tx);
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    land(&mut app, sessions);
    (app, rx)
}

/// The refresh in flight answers with `sessions`, all on one page.
fn land(app: &mut App, sessions: Vec<Session>) {
    let generation = app.sessions_page.generation;
    let page = sessions_page::Event::Page {
        generation,
        sessions,
        next: None,
    };
    sessions_page::apply_event(app, &stub(), page);
}

fn press(app: &mut App, code: KeyCode) {
    keys::handle_key(app, &stub(), KeyEvent::from(code));
}

fn highlighted(app: &App) -> Option<String> {
    sessions_page::highlighted_id(app)
}

fn three() -> Vec<Session> {
    vec![session("s1", 30), session("s2", 20), session("s3", 10)]
}

#[tokio::test]
async fn esc_then_left_lands_on_the_same_row_with_the_same_filters() {
    let (mut app, _rx) = listed(three());
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Char('s'));
    press(&mut app, KeyCode::Esc);
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    // The kept listing shows at once, before the refresh answers.
    assert_eq!(highlighted(&app).as_deref(), Some("s3"));
    assert!(app.sessions_page.loading && app.sessions_page.reloading);
    assert_eq!(app.sessions_page.browse.sort, sessions_page::Sort::Created);
    land(&mut app, three());
    assert_eq!(highlighted(&app).as_deref(), Some("s3"));
    assert!(!app.sessions_page.loading);
}

#[tokio::test]
async fn a_refresh_keeps_the_listed_rows_until_its_last_page() {
    let (mut app, _rx) = listed(three());
    press(&mut app, KeyCode::Esc);
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    let generation = app.sessions_page.generation;
    let first = sessions_page::Event::Page {
        generation,
        sessions: vec![session("new", 40)],
        next: Some("more".into()),
    };
    sessions_page::apply_event(&mut app, &stub(), first);
    assert_eq!(
        app.sessions_page.view.rows.len(),
        3,
        "no half listing shows"
    );
    let last = sessions_page::Event::Page {
        generation,
        sessions: three(),
        next: None,
    };
    sessions_page::apply_event(&mut app, &stub(), last);
    assert_eq!(app.sessions_page.view.rows.len(), 4);
}

fn opened(id: &str) -> PublicSessionState {
    serde_json::from_value(serde_json::json!({
        "eventId": 1,
        "session": {"id": id, "cwd": "/work/app"},
        "history": [],
    }))
    .unwrap()
}

#[tokio::test]
async fn back_from_an_opened_session_lands_on_it() {
    let (mut app, _rx) = listed(three());
    press(&mut app, KeyCode::Down);
    app.sessions_page.busy = Some("s2".into());
    let ticket = app.sessions_page.ticket;
    let state = Box::new(opened("s2"));
    actions::apply_event(&mut app, &stub(), Event::Opened(ticket, Ok(state)));
    assert!(!app.sessions_page.open);
    // Back with Left: the opened session is now the current one, highlighted.
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    assert_eq!(highlighted(&app).as_deref(), Some("s2"));
    // A listing that moves it (its bumped activity) keeps the highlight on it.
    land(
        &mut app,
        vec![session("s2", 50), session("s1", 30), session("s3", 10)],
    );
    assert_eq!(highlighted(&app).as_deref(), Some("s2"));
}

#[tokio::test]
async fn a_session_opened_before_its_listing_is_highlighted_once_listed() {
    let (mut app, _rx) = listed(three());
    app.sessions_page.busy = Some("fresh".into());
    let ticket = app.sessions_page.ticket;
    let state = Box::new(opened("fresh"));
    actions::apply_event(&mut app, &stub(), Event::Opened(ticket, Ok(state)));
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    let mut sessions = three();
    sessions.push(session("fresh", 5));
    land(&mut app, sessions);
    assert_eq!(highlighted(&app).as_deref(), Some("fresh"));
}

#[tokio::test]
async fn the_listing_is_read_once_the_server_is_ready() {
    let mut app = App::default();
    let (tx, _rx) = mpsc::channel(64);
    app.events = Some(tx);
    app.set_status(Status::Ready);
    sessions_page::server_ready(&mut app, &stub());
    assert!(app.sessions_page.loading, "the first page is on its way");
    assert!(!app.sessions_page.open);
    let generation = app.sessions_page.generation;
    // Left while it loads reuses that read instead of starting another.
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    assert_eq!(app.sessions_page.generation, generation);
    land(&mut app, three());
    assert_eq!(app.sessions_page.view.rows.len(), 3);
}

#[tokio::test]
async fn gone_folders_hide_until_g() {
    let mut sessions = three();
    sessions[1].cwd_missing = true;
    let (mut app, _rx) = listed(sessions);
    assert_eq!(app.sessions_page.view.rows.len(), 2);
    press(&mut app, KeyCode::Char('g'));
    assert_eq!(app.sessions_page.view.rows.len(), 3);
    press(&mut app, KeyCode::Esc);
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    assert!(app.sessions_page.browse.gone, "the toggle is kept too");
}

#[tokio::test]
async fn an_archive_during_a_refresh_is_not_undone_by_it() {
    let (mut app, _rx) = listed(three());
    press(&mut app, KeyCode::Esc);
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    let stale = app.sessions_page.generation;
    let archived = Event::Archived {
        id: "s2".into(),
        answer: Ok(Some(1)),
    };
    actions::apply_event(&mut app, &stub(), archived);
    // The refresh read before the archive lands late: it is dropped.
    let page = sessions_page::Event::Page {
        generation: stale,
        sessions: three(),
        next: None,
    };
    sessions_page::apply_event(&mut app, &stub(), page);
    assert_eq!(app.sessions_page.view.rows.len(), 2);
}
