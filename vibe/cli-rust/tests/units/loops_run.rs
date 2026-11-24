//! `/loop` manager `r`: runs the highlighted loop now through `loops/run`; the row shows it due and keeps its place.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::loops::{self, Event};
use vibe_rs::server::{notification, Client, ScheduledLoop};
use vibe_rs::ui;

use crate::support::{key, notify, render, rows};

fn with_loops(loops: Value) -> App {
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    app.set_status(Status::Ready);
    notify(
        &mut app,
        notification::SESSION_SNAPSHOT,
        json!({"sessionId": "session", "state": {
            "eventId": 1, "session": {"id": "session", "loops": loops}, "history": [],
        }}),
    );
    loops::open(&mut app);
    app
}

fn every(id: &str, prompt: &str, in_seconds: f64) -> Value {
    json!({"id": id, "prompt": prompt, "intervalSeconds": 3600,
           "nextFireAt": vibe_rs::utils::now_unix() as f64 + in_seconds})
}

fn screen(app: &mut App) -> String {
    rows(&render(100, 30, |frame| ui::draw(app, frame))).join("\n")
}

fn ran(result: Result<ScheduledLoop, String>) -> Event {
    Event::Ran {
        session_id: "session".into(),
        result,
    }
}

#[tokio::test]
async fn r_asks_the_server_to_run_the_highlighted_loop() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (tx, _rx) = mpsc::channel(8);
    let mut app = with_loops(json!([
        every("a", "First\nx", 60.0),
        every("b", "Second\ny", 120.0),
    ]));
    app.events = Some(tx);
    let listed = screen(&mut app);
    assert!(listed.contains("r run now"), "{listed}");
    loops::handle_key(&mut app, &client, key(KeyCode::Down));
    loops::handle_key(&mut app, &client, key(KeyCode::Char('r')));
    let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("the run is sent")
        .expect("a frame");
    assert_eq!(frame["method"], "loops/run");
    assert_eq!(
        frame["params"],
        json!({"sessionId": "session", "loopId": "b"})
    );
    assert_eq!(app.loops.running.as_deref(), Some("b"));
}

#[test]
fn a_run_loop_shows_due_in_its_row_and_stays_highlighted() {
    let mut app = with_loops(json!([
        every("a", "First\nx", 60.0),
        every("b", "Second\ny", 3_000.0),
    ]));
    loops::navigate(&mut app, true);
    app.loops.running = Some("b".into());
    let due: ScheduledLoop = serde_json::from_value(every("b", "Second\ny", -1.0)).unwrap();
    loops::apply_event(&mut app, ran(Ok(due)));
    assert_eq!(app.loops.running, None);
    let ids: Vec<_> = loops::rows(&app).iter().map(|l| l.id.clone()).collect();
    assert_eq!(ids, ["a", "b"], "the open list keeps its order");
    assert_eq!(loops::selected(&app).map(|l| l.id.as_str()), Some("b"));
    let screen = screen(&mut app);
    let row = screen.lines().find(|row| row.contains("Second")).unwrap();
    assert!(row.contains("due"), "{screen}");
}

#[test]
fn a_refused_run_says_why_and_keeps_the_loop() {
    let mut app = with_loops(json!([every("a", "First\nx", 60.0)]));
    app.loops.running = Some("a".into());
    loops::apply_event(&mut app, ran(Err("No scheduled loop with id `a`.".into())));
    assert_eq!(app.loops.running, None);
    assert_eq!(app.session.loops.len(), 1);
    let screen = screen(&mut app);
    assert!(
        screen.contains("Failed to run loop: No scheduled loop"),
        "{screen}"
    );
}

#[test]
fn r_on_an_empty_list_or_in_the_form_runs_nothing() {
    let client = Arc::new(Client::stub());
    let mut empty = with_loops(json!([]));
    loops::handle_key(&mut empty, &client, key(KeyCode::Char('r')));
    assert_eq!(empty.loops.running, None);
    assert!(!screen(&mut empty).contains("run now"));
    let mut app = with_loops(json!([every("a", "First\nx", 60.0)]));
    loops::handle_key(&mut app, &client, key(KeyCode::Char('n')));
    loops::handle_key(&mut app, &client, key(KeyCode::Char('r')));
    assert_eq!(app.loops.running, None);
    assert_eq!(app.loops.form.as_ref().unwrap().title.text, "r");
}
