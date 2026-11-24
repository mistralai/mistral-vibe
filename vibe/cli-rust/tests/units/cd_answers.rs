//! `/cd` answers: every move the server made is followed, a superseded failure is dropped.

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::commands::cd::{apply_event, Event};
use vibe_rs::server::RpcError;

use crate::feedback_support::notice_text;

fn attached_in(cwd: &str) -> App {
    let mut app = App::default();
    app.set_session_id("session".into());
    app.session.cwd = Some(cwd.into());
    app.session.agent_config.cwd = Some(cwd.into());
    app
}

fn moved(ticket: u64, cwd: &str) -> Event {
    let state = json!({ "state": { "session": { "cwd": cwd } } });
    Event::new("session".into(), ticket, cwd.into(), Ok(state))
}

fn failed(ticket: u64, target: &str) -> Event {
    let error = RpcError {
        code: json!("invalid_params"),
        message: "Relocation failed".into(),
        data: None,
    };
    Event::new("session".into(), ticket, target.into(), Err(error))
}

#[test]
fn an_earlier_move_that_landed_is_followed_when_the_later_one_fails() {
    let mut app = attached_in("/work/origin");
    // `/cd /work/a` then `/cd /work/b`, both in flight.
    app.session.cd_ticket = 2;
    apply_event(&mut app, moved(1, "/work/a"));
    apply_event(&mut app, failed(2, "/work/b"));
    assert_eq!(app.session.cwd.as_deref(), Some("/work/a"));
    assert_eq!(app.session.previous_cwd.as_deref(), Some("/work/origin"));
    assert_eq!(notice_text(&app), Some("Relocation failed"));
}

#[test]
fn the_last_move_to_land_is_the_folder_shown() {
    let mut app = attached_in("/work/origin");
    app.session.cd_ticket = 2;
    apply_event(&mut app, moved(1, "/work/a"));
    apply_event(&mut app, moved(2, "/work/b"));
    assert_eq!(app.session.cwd.as_deref(), Some("/work/b"));
    assert_eq!(app.session.previous_cwd.as_deref(), Some("/work/a"));
}

#[test]
fn a_superseded_failure_shows_nothing() {
    let mut app = attached_in("/work/origin");
    app.session.cd_ticket = 2;
    apply_event(&mut app, failed(1, "/work/a"));
    assert_eq!(notice_text(&app), None);
    assert_eq!(app.session.cwd.as_deref(), Some("/work/origin"));
}

#[test]
fn an_answer_for_a_session_switched_away_from_is_dropped() {
    let mut app = attached_in("/work/origin");
    app.set_session_id("other".into());
    app.session.cd_ticket = 1;
    apply_event(&mut app, moved(1, "/work/a"));
    assert_eq!(app.session.cwd.as_deref(), Some("/work/origin"));
}
