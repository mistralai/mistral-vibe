//! `/loop` manager and next-loop row: schedules, the list, delete, the add form and the idle row.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::loops::{self, schedule, Event};
use vibe_rs::server::{notification, Client, ScheduledLoop};
use vibe_rs::ui;

use crate::support::{key, notify, render, rows};

fn scheduled(id: &str, prompt: &str, in_seconds: f64) -> Value {
    json!({
        "id": id, "prompt": prompt, "intervalSeconds": 1200,
        "nextFireAt": vibe_rs::utils::now_unix() as f64 + in_seconds,
    })
}

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
    app
}

fn screen(app: &mut App, width: u16) -> String {
    rows(&render(width, 24, |frame| ui::draw(app, frame))).join("\n")
}

fn type_text(app: &mut App, client: &Arc<Client>, text: &str) {
    for c in text.chars() {
        loops::handle_key(app, client, key(KeyCode::Char(c)));
    }
}

#[test]
fn a_schedule_reads_as_an_interval_or_common_cron_words() {
    let read = |interval: Option<u64>, cron: Option<&str>| {
        schedule::recurrence(&ScheduledLoop {
            id: "l".into(),
            prompt: "p".into(),
            interval_seconds: interval,
            cron: cron.map(str::to_owned),
            next_fire_at: 0.0,
        })
    };
    assert_eq!(read(Some(1200), None), "every 20m");
    assert_eq!(read(Some(90), None), "every 90s");
    assert_eq!(read(Some(7200), None), "every 2h");
    assert_eq!(read(Some(86_400), None), "every 1d");
    assert_eq!(read(None, Some("0 9 * * 1-5")), "weekdays 09:00");
    assert_eq!(read(None, Some("30 18 * * *")), "daily 18:30");
    assert_eq!(read(None, Some("15 * * * *")), "hourly at :15");
    assert_eq!(
        read(None, Some("*/20 9-18 * * 1-5")),
        "cron */20 9-18 * * 1-5"
    );
}

#[test]
fn the_next_run_rounds_coarser_the_farther_it_is() {
    let cases = [
        (-5.0, "due"),
        (12.0, "in <1m"),
        (60.0, "in 1m"),
        (61.0, "in 2m"),
        (3_540.0, "in 59m"),
        (3_541.0, "in ~1h"),
        (5_500.0, "in ~2h"),
        (86_400.0 * 3.2, "in ~3d"),
    ];
    for (seconds, expected) in cases {
        assert_eq!(schedule::next_run(seconds), expected, "{seconds}");
    }
    // The text only changes on the minute while under an hour away.
    assert_eq!(schedule::next_change(290.0), 50.0);
    assert_eq!(schedule::next_change(-1.0), f64::INFINITY);
}

#[test]
fn the_redraw_wakes_exactly_when_the_next_run_text_changes() {
    let seconds = [
        1.0,
        30.0,
        59.9,
        60.0,
        61.0,
        120.0,
        290.0,
        3_540.0,
        3_541.0,
        3_600.0,
        5_400.0,
        7_200.0,
        84_600.0,
        86_399.0,
        86_400.0,
        129_600.0,
        172_800.0,
        86_400.0 * 3.2,
    ];
    // It wakes once the text changed, and at most 20ms after it does.
    for left in seconds {
        let wait = schedule::next_change(left);
        let shown = schedule::next_run(left);
        let woken = schedule::next_run(left - wait);
        assert_ne!(woken, shown, "{left}: still {shown} after {wait}s");
        let just_before = schedule::next_run(left - (wait - 0.02).max(0.0));
        assert_eq!(just_before, shown, "{left}: changed long before {wait}s");
    }
}

#[test]
fn a_due_loop_does_not_stop_later_rows_from_updating() {
    let at = |id: &str, next_fire_at: f64| ScheduledLoop {
        id: id.into(),
        prompt: "p".into(),
        interval_seconds: Some(600),
        cron: None,
        next_fire_at,
    };
    let now = 1_000.0;
    let loops = [at("due", now - 5.0), at("later", now + 290.0)];
    assert_eq!(schedule::next_redraw(&loops, now), 50.0);
    assert_eq!(schedule::next_redraw(&loops[..1], now), f64::INFINITY);
    assert_eq!(schedule::next_redraw(&[], now), f64::INFINITY);
}

#[test]
fn the_idle_row_names_the_soonest_loop_and_its_title() {
    let mut app = with_loops(json!([
        scheduled("late", "Nightly report\nSummarize the day.", 3_600.0),
        scheduled("soon", "PR watch #42\nCheck the PR's CI.", 290.0),
    ]));
    let screen = screen(&mut app, 120);
    assert!(
        screen.contains("◷ next loop in 5m: PR watch #42"),
        "{screen}"
    );
    assert!(!screen.contains("Nightly report"), "{screen}");
    assert!(!screen.contains("Check the PR"), "{screen}");
}

#[test]
fn the_idle_row_cuts_the_title_then_drops_it_on_a_narrow_screen() {
    let title = "Review every open pull request on the dashboard repository";
    let mut app = with_loops(json!([scheduled("l", title, 290.0)]));
    let row = |app: &mut App, width: u16| {
        rows(&render(width, 24, |frame| ui::draw(app, frame)))
            .into_iter()
            .find(|row| row.contains("next loop"))
            .expect("the next-loop row")
            .trim()
            .to_owned()
    };
    assert_eq!(row(&mut app, 120), format!("◷ next loop in 5m: {title}"));
    let cut = row(&mut app, 40);
    assert!(cut.starts_with("◷ next loop in 5m: Review"), "{cut}");
    assert!(cut.ends_with('…'), "{cut}");
    assert_eq!(row(&mut app, 24), "◷ next loop in 5m");
}

#[test]
fn no_next_loop_row_while_a_turn_runs() {
    let mut app = with_loops(json!([scheduled("l", "Build watch\nCheck it.", 290.0)]));
    app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
    assert!(!screen(&mut app, 120).contains("next loop"));
}

#[test]
fn a_session_update_replaces_the_loops_and_a_subagent_s_does_not() {
    let mut app = with_loops(json!([]));
    let patch = |session: &str, loops: Value| {
        json!({"sessionId": session, "eventId": 2,
               "patch": [{"op": "replace", "path": "/loops", "value": loops}]})
    };
    let child = patch("child", json!([scheduled("c", "Child loop\nx", 60.0)]));
    notify(&mut app, notification::SESSION_UPDATED, child);
    assert!(app.session.loops.is_empty());
    let own = patch("session", json!([scheduled("a", "Build watch\nx", 60.0)]));
    notify(&mut app, notification::SESSION_UPDATED, own);
    assert_eq!(app.session.loops.len(), 1);
    assert_eq!(app.session.loops[0].id, "a");
}

#[test]
fn the_manager_lists_each_loop_with_its_schedule_and_the_highlighted_instructions() {
    let mut app = with_loops(json!([
        scheduled("late", "Nightly report\nSummarize the day.", 3_600.0),
        scheduled("soon", "PR watch #42\nCheck the PR's CI.", 290.0),
    ]));
    loops::open(&mut app);
    let screen = screen(&mut app, 100);
    let first = screen.find("PR watch #42").expect("soonest first");
    assert!(
        first < screen.find("Nightly report").expect("later loop"),
        "{screen}"
    );
    assert!(screen.contains("every 20m"), "{screen}");
    assert!(screen.contains("in 5m"), "{screen}");
    assert!(screen.contains("Check the PR's CI."), "{screen}");
    assert!(!screen.contains("Summarize the day."), "{screen}");
    assert!(screen.contains("d delete"), "{screen}");
    assert!(screen.contains("n new"), "{screen}");
}

#[test]
fn an_empty_manager_offers_to_add_a_loop() {
    let mut app = with_loops(json!([]));
    loops::open(&mut app);
    let screen = screen(&mut app, 100);
    assert!(screen.contains("No scheduled loops."), "{screen}");
    assert!(screen.contains("n new"), "{screen}");
    assert!(!screen.contains("d delete"), "{screen}");
}

#[tokio::test]
async fn d_deletes_the_highlighted_loop_at_once() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (tx, _rx) = mpsc::channel(8);
    let mut app = with_loops(json!([
        scheduled("a", "First\nx", 60.0),
        scheduled("b", "Second\nx", 120.0),
    ]));
    app.events = Some(tx);
    loops::open(&mut app);
    loops::handle_key(&mut app, &client, key(KeyCode::Down));
    loops::handle_key(&mut app, &client, key(KeyCode::Char('d')));
    let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("the delete is sent")
        .expect("a frame");
    assert_eq!(frame["method"], "loops/delete");
    assert_eq!(
        frame["params"],
        json!({"sessionId": "session", "loopId": "b"})
    );
    assert_eq!(app.loops.deleting.as_deref(), Some("b"));

    loops::apply_event(
        &mut app,
        Event::Deleted {
            session_id: "session".into(),
            id: "b".into(),
            error: None,
        },
    );
    let ids: Vec<_> = app.session.loops.iter().map(|l| l.id.as_str()).collect();
    assert_eq!(ids, ["a"]);
    assert_eq!(app.loops.list.selected(), 0);
}

#[test]
fn a_failed_delete_keeps_the_loop_and_says_why() {
    let mut app = with_loops(json!([scheduled("a", "First\nx", 60.0)]));
    loops::open(&mut app);
    app.loops.deleting = Some("a".into());
    let error = Some("No scheduled loop with id `a`.".into());
    loops::apply_event(
        &mut app,
        Event::Deleted {
            session_id: "session".into(),
            id: "a".into(),
            error,
        },
    );
    assert_eq!(app.session.loops.len(), 1);
    assert_eq!(app.loops.deleting, None);
    let screen = screen(&mut app, 100);
    assert!(
        screen.contains("Failed to delete loop: No scheduled loop"),
        "{screen}"
    );
}

#[tokio::test]
async fn the_form_schedules_an_interval_or_a_cron_with_the_title_line() {
    for (every, key_name, value) in [
        ("Every 10m", "interval", "10m"),
        ("0  9 * * 1-5", "cron", "0 9 * * 1-5"),
    ] {
        let (client, mut frames) = Client::stub_answering();
        let client = Arc::new(client);
        let (tx, _rx) = mpsc::channel(8);
        let mut app = with_loops(json!([]));
        app.events = Some(tx);
        loops::open(&mut app);
        loops::handle_key(&mut app, &client, key(KeyCode::Char('n')));
        type_text(&mut app, &client, "Build watch");
        loops::handle_key(&mut app, &client, key(KeyCode::Tab));
        type_text(&mut app, &client, every);
        loops::handle_key(&mut app, &client, key(KeyCode::Tab));
        loops::paste(&mut app, "Check the build\r\nthen report");
        loops::handle_key(&mut app, &client, key(KeyCode::Enter));
        let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
            .await
            .expect("the create is sent")
            .expect("a frame");
        assert_eq!(frame["method"], "loops/create");
        assert_eq!(
            frame["params"],
            json!({"sessionId": "session", key_name: value,
                   "prompt": "Build watch\nCheck the build\nthen report"})
        );
        assert!(app
            .loops
            .form
            .as_ref()
            .is_some_and(|form| form.submission.is_some()));
    }
}

#[test]
fn an_incomplete_form_says_what_is_missing_and_sends_nothing() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([]));
    loops::open(&mut app);
    loops::handle_key(&mut app, &client, key(KeyCode::Char('n')));
    loops::handle_key(&mut app, &client, key(KeyCode::Enter));
    assert_eq!(app.loops.error.as_deref(), Some("Give the loop a title."));
    type_text(&mut app, &client, "Build watch");
    loops::handle_key(&mut app, &client, key(KeyCode::Tab));
    type_text(&mut app, &client, "every ten minutes");
    loops::handle_key(&mut app, &client, key(KeyCode::Tab));
    type_text(&mut app, &client, "Check it");
    loops::handle_key(&mut app, &client, key(KeyCode::Enter));
    let error = app.loops.error.clone().unwrap_or_default();
    assert!(error.starts_with("Use an interval such as 10m"), "{error}");
    assert!(app
        .loops
        .form
        .as_ref()
        .is_some_and(|form| !form.submission.is_some()));
}

#[test]
fn without_an_event_loop_nothing_waits_for_an_answer() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([scheduled("a", "Build", 60.0)]));
    loops::open(&mut app);
    loops::handle_key(&mut app, &client, key(KeyCode::Char('d')));
    assert_eq!(app.loops.deleting, None);
    loops::handle_key(&mut app, &client, key(KeyCode::Char('n')));
    type_text(&mut app, &client, "Build watch");
    loops::handle_key(&mut app, &client, key(KeyCode::Tab));
    type_text(&mut app, &client, "10m");
    loops::handle_key(&mut app, &client, key(KeyCode::Tab));
    type_text(&mut app, &client, "Check it");
    loops::handle_key(&mut app, &client, key(KeyCode::Enter));
    assert!(app
        .loops
        .form
        .as_ref()
        .is_some_and(|form| !form.submission.is_some()));
}

#[test]
fn a_refused_loop_keeps_the_form_open_with_the_server_s_reason() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([]));
    loops::open(&mut app);
    loops::handle_key(&mut app, &client, key(KeyCode::Char('n')));
    app.loops.form.as_mut().unwrap().submission = Some(1);
    let reason = "Interval must be at least 30s.".to_owned();
    loops::apply_event(&mut app, created(1, Err(reason)));
    assert!(app
        .loops
        .form
        .as_ref()
        .is_some_and(|form| !form.submission.is_some()));
    let screen = screen(&mut app, 100);
    assert!(screen.contains("New loop"), "{screen}");
    assert!(
        screen.contains("Interval must be at least 30s."),
        "{screen}"
    );
}

#[test]
fn a_created_loop_closes_the_form_and_highlights_it() {
    let mut app = with_loops(json!([scheduled("a", "First\nx", 60.0)]));
    loops::open(&mut app);
    app.loops.form = Some(Default::default());
    app.loops.form.as_mut().unwrap().submission = Some(1);
    let loop_b: ScheduledLoop = serde_json::from_value(scheduled("b", "Second\nx", 30.0)).unwrap();
    loops::apply_event(&mut app, created(1, Ok(loop_b)));
    assert!(app.loops.form.is_none());
    assert_eq!(loops::selected(&app).map(|l| l.id.as_str()), Some("b"));
}

fn created(submission: u64, result: Result<ScheduledLoop, String>) -> Event {
    Event::Created {
        session_id: "session".into(),
        submission,
        result,
    }
}

#[test]
fn a_late_answer_for_a_closed_form_or_another_session_is_dropped() {
    let mut app = with_loops(json!([scheduled("a", "First\nx", 60.0)]));
    loops::open(&mut app);
    let loop_b =
        || -> ScheduledLoop { serde_json::from_value(scheduled("b", "Second\nx", 30.0)).unwrap() };
    app.loops.form = Some(Default::default());
    app.loops.form.as_mut().unwrap().submission = Some(2);
    loops::apply_event(&mut app, created(1, Ok(loop_b())));
    assert!(
        app.loops.form.is_some(),
        "an older submission keeps the new form"
    );
    assert_eq!(app.session.loops.len(), 1);
    let elsewhere = Event::Created {
        session_id: "other".into(),
        submission: 2,
        result: Ok(loop_b()),
    };
    loops::apply_event(&mut app, elsewhere);
    assert!(
        app.loops.form.is_some(),
        "another session's answer is dropped"
    );
    assert_eq!(app.session.loops.len(), 1);
    let deleted_elsewhere = Event::Deleted {
        session_id: "other".into(),
        id: "a".into(),
        error: None,
    };
    loops::apply_event(&mut app, deleted_elsewhere);
    assert_eq!(app.session.loops.len(), 1);
}

#[test]
fn esc_leaves_the_form_then_closes_the_manager() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([]));
    loops::open(&mut app);
    loops::handle_key(&mut app, &client, key(KeyCode::Char('n')));
    loops::handle_key(&mut app, &client, key(KeyCode::Esc));
    assert!(app.loops.open && app.loops.form.is_none());
    loops::handle_key(&mut app, &client, key(KeyCode::Esc));
    assert!(!app.loops.open);
}

#[test]
fn j_and_k_type_in_the_form_but_navigate_the_list() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([
        scheduled("a", "First\nx", 60.0),
        scheduled("b", "Second\nx", 120.0),
    ]));
    loops::open(&mut app);
    loops::handle_key(&mut app, &client, key(KeyCode::Char('j')));
    assert_eq!(app.loops.list.selected(), 1);
    loops::handle_key(&mut app, &client, key(KeyCode::Char('n')));
    type_text(&mut app, &client, "jk");
    assert_eq!(app.loops.form.as_ref().unwrap().title.text, "jk");
    let ctrl_d = KeyEvent::new(KeyCode::Char('d'), KeyModifiers::CONTROL);
    loops::handle_key(&mut app, &client, ctrl_d);
    assert!(app.loops.form.is_some());
}

#[test]
fn bare_loop_opens_the_manager_and_loop_with_arguments_stays_a_prompt() {
    use vibe_rs::input_modes::{classify, ClassifiedInput};
    assert_eq!(
        classify("/loop", &[]),
        ClassifiedInput::SlashCommand { command: "/loop" }
    );
    assert_eq!(
        classify("/loop 10m check CI", &[]),
        ClassifiedInput::Prompt {
            text: "/loop 10m check CI".into()
        }
    );
    assert!(vibe_rs::commands::is_side_channel("/loop"));
}

#[test]
fn an_open_manager_hides_the_idle_next_loop_row() {
    let mut app = with_loops(json!([scheduled("l", "Build watch\nCheck it.", 290.0)]));
    loops::open(&mut app);
    assert!(!screen(&mut app, 120).contains("next loop"));
}

#[test]
fn the_manager_columns_line_up() {
    let mut app = with_loops(json!([
        scheduled("soon", "PR watch #42\nx", 290.0),
        {"id": "w", "prompt": "Morning review\nx", "cron": "0 9 * * 1-5",
         "nextFireAt": vibe_rs::utils::now_unix() as f64 + 7_200.0},
    ]));
    loops::open(&mut app);
    let screen = screen(&mut app, 100);
    let column = |needle: &str| {
        screen
            .lines()
            .find_map(|line| line.find(needle).map(|at| line[..at].chars().count()))
            .unwrap_or_else(|| panic!("{needle}\n{screen}"))
    };
    assert_eq!(column("every 20m"), column("weekdays 09:00"), "{screen}");
    assert_eq!(column("in 5m"), column("in ~2h"), "{screen}");
}

fn ids(app: &App) -> Vec<String> {
    loops::rows(app)
        .iter()
        .map(|scheduled| scheduled.id.clone())
        .collect()
}

fn updated(app: &mut App, loops: Value) {
    notify(
        app,
        notification::SESSION_UPDATED,
        json!({"sessionId": "session", "eventId": 2,
               "patch": [{"op": "replace", "path": "/loops", "value": loops}]}),
    );
}

#[test]
fn an_open_manager_keeps_its_order_until_reopened() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([
        scheduled("a", "First\nx", 60.0),
        scheduled("b", "Second\nx", 120.0),
    ]));
    loops::open(&mut app);
    loops::handle_key(&mut app, &client, key(KeyCode::Down));
    // `a` ran and is due again after `b`; a new loop `c` is due first.
    updated(
        &mut app,
        json!([
            scheduled("a", "First\nx", 1_200.0),
            scheduled("b", "Second\nx", 120.0),
            scheduled("c", "Third\nx", 30.0),
        ]),
    );
    assert_eq!(ids(&app), ["a", "b", "c"], "rows stay put, new ones append");
    assert_eq!(loops::selected(&app).map(|l| l.id.as_str()), Some("b"));
    // The idle row still names the soonest loop.
    assert_eq!(app.session.loops[0].id, "c");
    let ctrl_r = KeyEvent::new(KeyCode::Char('r'), KeyModifiers::CONTROL);
    loops::handle_key(&mut app, &client, ctrl_r);
    assert_eq!(ids(&app), ["a", "b", "c"], "no key re-sorts an open list");
    loops::close(&mut app);
    loops::open(&mut app);
    assert_eq!(ids(&app), ["c", "b", "a"], "reopening sorts soonest first");
}

#[test]
fn a_loop_deleted_elsewhere_leaves_the_open_list() {
    let mut app = with_loops(json!([
        scheduled("a", "First\nx", 60.0),
        scheduled("b", "Second\nx", 120.0),
    ]));
    loops::open(&mut app);
    updated(&mut app, json!([scheduled("b", "Second\nx", 120.0)]));
    assert_eq!(ids(&app), ["b"]);
    assert_eq!(loops::selected(&app).map(|l| l.id.as_str()), Some("b"));
}

#[test]
fn the_instructions_wrap_in_full_under_the_rows_on_a_narrow_screen() {
    let long = "Check the PR's CI and reviews, then summarize every new comment and failing check in a short report.";
    let mut app = with_loops(json!([scheduled("a", &format!("PR watch\n{long}"), 60.0)]));
    loops::open(&mut app);
    let screen = screen(&mut app, 60);
    let rows: Vec<&str> = screen.lines().collect();
    let title = rows
        .iter()
        .position(|row| row.contains("PR watch"))
        .expect("the row");
    assert!(
        rows[title + 1]
            .trim_matches(['│', ' '])
            .chars()
            .all(|c| c == '─')
            && rows[title + 1].contains('─'),
        "a rule parts them\n{screen}"
    );
    assert!(rows[title + 2].contains("Check the PR's CI"), "{screen}");
    assert!(
        screen.contains("short report."),
        "wrapped, not cut\n{screen}"
    );
    assert!(!screen.contains('…'), "{screen}");
}

#[test]
fn the_instructions_sit_beside_the_rows_on_a_wide_screen() {
    let mut app = with_loops(json!([scheduled(
        "a",
        "PR watch\nCheck the PR's CI.",
        60.0
    )]));
    loops::open(&mut app);
    let screen = screen(&mut app, 160);
    let row = screen
        .lines()
        .find(|row| row.contains("PR watch"))
        .expect("the row");
    let (list, details) = row.split_once("Check the PR's CI.").expect("beside");
    assert!(
        list.trim_end().ends_with('│') && list.matches('│').count() == 2,
        "a rule parts them\n{screen}"
    );
    assert!(!details.is_empty(), "{screen}");
}

#[test]
fn the_box_fits_the_longest_instructions_and_keeps_that_height() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([
        scheduled("a", "Say hi\nSay hi to the user.", 60.0),
        scheduled("b", "Review\nOne.\nTwo.\nThree.", 120.0),
    ]));
    loops::open(&mut app);
    let first = screen(&mut app, 80);
    let blank = |screen: &str| {
        screen
            .lines()
            .skip_while(|row| !row.contains('─') || row.contains('┌'))
            .take_while(|row| !row.contains("navigate"))
            .filter(|row| row.trim_matches(['│', ' ']).is_empty())
            .count()
    };
    assert_eq!(
        blank(&first),
        3,
        "3 lines for the longest, 1 blank before the footer\n{first}"
    );
    loops::handle_key(&mut app, &client, key(KeyCode::Down));
    let second = screen(&mut app, 80);
    assert!(second.contains("Three."), "{second}");
    assert_eq!(
        first.lines().position(|row| row.contains('┌')),
        second.lines().position(|row| row.contains('┌')),
        "{second}"
    );
}

#[test]
fn page_down_scrolls_long_instructions_and_another_loop_starts_at_the_top() {
    let client = Arc::new(Client::stub());
    let body: Vec<String> = (1..=30).map(|n| format!("Step {n:02}.")).collect();
    let prompt = format!("Long\n{}", body.join("\n"));
    let mut app = with_loops(json!([
        scheduled("a", &prompt, 60.0),
        scheduled("b", "Short\nOne step.", 120.0),
    ]));
    loops::open(&mut app);
    let _ = screen(&mut app, 80);
    loops::handle_key(&mut app, &client, key(KeyCode::PageDown));
    let scrolled = screen(&mut app, 80);
    assert!(!scrolled.contains("Step 01."), "{scrolled}");
    loops::handle_key(&mut app, &client, key(KeyCode::Down));
    assert!(screen(&mut app, 80).contains("One step."));
    loops::handle_key(&mut app, &client, key(KeyCode::Up));
    let back = screen(&mut app, 80);
    assert!(back.contains("Step 01."), "{back}");
}

#[test]
fn an_empty_form_describes_each_field() {
    let client = Arc::new(Client::stub());
    let mut app = with_loops(json!([]));
    loops::open(&mut app);
    loops::handle_key(&mut app, &client, key(KeyCode::Char('n')));
    let empty = screen(&mut app, 120);
    for field in [
        "Title         A short name for the loop",
        "Every         10m, 2h, 1d, or a cron",
        "Instructions  The prompt sent to the agent each time the loop runs",
    ] {
        assert!(empty.contains(field), "{field}\n{empty}");
    }
    assert!(
        empty.contains("↑↓/Tab navigate  Enter create  Esc back"),
        "{empty}"
    );
    // A refusal shows under the fields, never over one.
    app.loops.error = Some("Interval must be at least 30s.".into());
    let refused = screen(&mut app, 120);
    let rows: Vec<&str> = refused.lines().collect();
    let at = rows.iter().position(|row| row.contains("Instructions"));
    let at = at.expect("the field");
    assert!(rows[at - 1].contains("Every"), "{refused}");
    assert!(
        rows[at + 1].contains("Interval must be at least 30s."),
        "{refused}"
    );
}

#[test]
fn a_deleted_loop_hands_its_row_to_the_next_however_the_news_arrives() {
    let three = || {
        json!([
            scheduled("a", "First\nx", 60.0),
            scheduled("b", "Second\nx", 120.0),
            scheduled("c", "Third\nx", 180.0),
        ])
    };
    let left = json!([
        scheduled("a", "First\nx", 60.0),
        scheduled("c", "Third\nx", 180.0)
    ]);
    for update_first in [true, false] {
        let client = Arc::new(Client::stub());
        let mut app = with_loops(three());
        loops::open(&mut app);
        loops::handle_key(&mut app, &client, key(KeyCode::Down));
        let deleted = Event::Deleted {
            session_id: "session".into(),
            id: "b".into(),
            error: None,
        };
        match update_first {
            true => {
                updated(&mut app, left.clone());
                loops::apply_event(&mut app, deleted);
            }
            false => {
                loops::apply_event(&mut app, deleted);
                updated(&mut app, left.clone());
            }
        }
        let highlighted = loops::selected(&app).map(|l| l.id.clone());
        assert_eq!(
            highlighted.as_deref(),
            Some("c"),
            "update first: {update_first}"
        );
    }
}
