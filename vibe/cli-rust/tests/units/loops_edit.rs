//! `/loop` manager edits: Enter or `e` fills the form from the highlighted loop and saves it with `loops/update`.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::loops::{self, Event, FormField};
use vibe_rs::server::{notification, Client, ScheduledLoop};
use vibe_rs::ui;

use crate::support::{self, ctrl, key, notify, render, rows};

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

fn every(id: &str, prompt: &str, interval: u64) -> Value {
    json!({"id": id, "prompt": prompt, "intervalSeconds": interval, "nextFireAt": 4e9})
}

fn screen(app: &mut App) -> String {
    rows(&render(100, 30, |frame| ui::draw(app, frame))).join("\n")
}

fn type_text(app: &mut App, client: &Arc<Client>, text: &str) {
    for c in text.chars() {
        loops::handle_key(app, client, key(KeyCode::Char(c)));
    }
}

fn fields(app: &App) -> [String; 3] {
    let form = app.loops.form.as_ref().expect("the form");
    [&form.title, &form.schedule, &form.instructions].map(|field| field.text.clone())
}

#[test]
fn enter_and_e_open_the_form_filled_from_the_highlighted_loop() {
    let client = Arc::new(Client::stub());
    let cron = json!({"id": "c", "prompt": "Standup\nSummarize\nthe night", "cron": "0 9 * * 1-5", "nextFireAt": 4e9});
    for open in [KeyCode::Enter, KeyCode::Char('e')] {
        let mut app = with_loops(json!([every("a", "Build watch\nCheck it", 600), cron]));
        loops::handle_key(&mut app, &client, key(KeyCode::Down));
        loops::handle_key(&mut app, &client, key(open));
        assert_eq!(
            fields(&app),
            ["Standup", "0 9 * * 1-5", "Summarize\nthe night"].map(String::from)
        );
        let screen = screen(&mut app);
        assert!(screen.contains("Edit loop"), "{screen}");
        assert!(screen.contains("Enter save"), "{screen}");
    }
    let mut app = with_loops(json!([every("a", "Build watch\nCheck it", 600)]));
    loops::handle_key(&mut app, &client, key(KeyCode::Char('e')));
    assert_eq!(
        fields(&app),
        ["Build watch", "10m", "Check it"].map(String::from)
    );
}

#[test]
fn the_list_hints_edit_and_an_empty_list_opens_no_form() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([every("a", "Build watch\nCheck it", 600)]));
    let listed = screen(&mut app);
    assert!(listed.contains("Enter/e edit"), "{listed}");
    let mut empty = with_loops(json!([]));
    loops::handle_key(&mut empty, &client, key(KeyCode::Enter));
    loops::handle_key(&mut empty, &client, key(KeyCode::Char('e')));
    assert!(empty.loops.form.is_none());
    assert!(!screen(&mut empty).contains("edit"));
}

#[tokio::test]
async fn saving_sends_loops_update_with_the_loop_id() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (tx, _rx) = mpsc::channel(8);
    let mut app = with_loops(json!([every("a", "Build watch\nCheck it", 600)]));
    app.events = Some(tx);
    loops::handle_key(&mut app, &client, key(KeyCode::Enter));
    loops::handle_key(&mut app, &client, key(KeyCode::Tab));
    loops::handle_key(&mut app, &client, ctrl('u'));
    loops::paste(&mut app, "1h");
    loops::handle_key(&mut app, &client, key(KeyCode::Enter));
    let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("the update is sent")
        .expect("a frame");
    assert_eq!(frame["method"], "loops/update");
    assert_eq!(
        frame["params"],
        json!({"sessionId": "session", "loopId": "a", "interval": "1h",
               "prompt": "Build watch\nCheck it"})
    );
}

#[test]
fn only_a_loop_without_instructions_may_save_without_them() {
    let mut app = with_loops(json!([every("a", "Check the build", 600)]));
    loops::edit(&mut app);
    let form = app.loops.form.as_ref().unwrap();
    assert_eq!(
        form.request("session").unwrap(),
        (
            "loops/update",
            json!({"sessionId": "session", "loopId": "a", "interval": "10m",
                   "prompt": "Check the build"})
        )
    );
    let mut app = with_loops(json!([every("a", "Build watch\nCheck it", 600)]));
    loops::edit(&mut app);
    let form = app.loops.form.as_mut().unwrap();
    form.instructions.text.clear();
    form.instructions.cursor = 0;
    assert_eq!(
        form.request("session").unwrap_err(),
        "Write the instructions the loop sends."
    );
}

#[test]
fn a_saved_loop_replaces_its_row_and_stays_highlighted() {
    let mut app = with_loops(json!([
        every("a", "First\nx", 600),
        every("b", "Second\nx", 1200),
    ]));
    loops::handle_key(&mut app, &Arc::new(Client::stub()), key(KeyCode::Down));
    loops::edit(&mut app);
    app.loops.form.as_mut().unwrap().submission = Some(1);
    let saved: ScheduledLoop = serde_json::from_value(every("b", "Renamed\ny", 3600)).unwrap();
    loops::apply_event(
        &mut app,
        Event::Saved {
            session_id: "session".into(),
            submission: 1,
            result: Ok(saved),
        },
    );
    assert!(app.loops.form.is_none());
    let ids: Vec<_> = loops::rows(&app).iter().map(|l| l.id.clone()).collect();
    assert_eq!(ids, ["a", "b"]);
    assert_eq!(
        loops::selected(&app).map(|l| l.prompt.as_str()),
        Some("Renamed\ny")
    );
    let screen = screen(&mut app);
    assert!(
        screen.contains("Renamed") && screen.contains("every 1h"),
        "{screen}"
    );
}

/// Where `text` is drawn on the last screen: its `(column, row)`.
fn find(screen: &str, text: &str) -> (u16, u16) {
    screen
        .lines()
        .enumerate()
        .find_map(|(row, line)| {
            let byte = line.find(text)?;
            Some((line[..byte].chars().count() as u16, row as u16))
        })
        .unwrap_or_else(|| panic!("{text:?} not on screen\n{screen}"))
}

#[test]
fn a_click_on_a_field_focuses_it_and_puts_the_caret_there() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([every(
        "a",
        "Build watch\nCheck it\nthen report",
        600
    )]));
    loops::handle_key(&mut app, &client, key(KeyCode::Enter));
    let drawn = screen(&mut app);
    let focused = |app: &App| app.loops.form.as_ref().unwrap().focused_field();

    let (column, row) = find(&drawn, "then report");
    support::click(&mut app, column + 4, row);
    assert_eq!(focused(&app), FormField::Instructions);
    type_text(&mut app, &client, "!");
    assert_eq!(fields(&app)[2], "Check it\nthen! report");

    let (column, row) = find(&drawn, "Build watch");
    support::click(&mut app, column, row);
    assert_eq!(focused(&app), FormField::Title);
    type_text(&mut app, &client, ">");
    assert_eq!(fields(&app)[0], ">Build watch");

    let (column, row) = find(&drawn, "Every");
    support::click(&mut app, column, row);
    assert_eq!(focused(&app), FormField::Schedule);
    assert_eq!(
        fields(&app)[1],
        "10m",
        "a click on the label keeps the text"
    );
}
