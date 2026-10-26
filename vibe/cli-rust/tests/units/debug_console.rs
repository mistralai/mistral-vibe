//! The debug console's decode, formatting, paging, and scroll-anchor logic.

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::Terminal;
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::commands::submission;
use vibe_rs::completion_manager::CompletionEntry;
use vibe_rs::config;
use vibe_rs::debug_console::{
    self, apply_event, apply_page, apply_polled, at_top, decode_log_message, entry_at, level_style,
    parse_page, scroll_down, scroll_for_key, scroll_home, timestamp_ms, total_visual, view,
    DebugConsole, Event, LogEntry, LogPage, Scroll, MAX_ENTRIES, MAX_VISUAL_LINES, PAGE_SIZE,
};
use vibe_rs::input;
use vibe_rs::mouse;
use vibe_rs::server::Client;
use vibe_rs::ui::theme;

fn entry(id: &str, level: &str, message: &str) -> LogEntry {
    LogEntry {
        id: id.to_owned(),
        timestamp: "2026-01-02 03:04:05".to_owned(),
        level: level.to_owned(),
        message: message.to_owned(),
    }
}

fn page(ids: &[&str], has_more: bool, cursor: Option<u64>) -> LogPage {
    // The wire serves pages newest-first.
    let entries: Vec<LogEntry> = ids
        .iter()
        .map(|id| entry(id, "INFO", &format!("message {id}")))
        .collect();
    LogPage {
        entries,
        has_more,
        cursor,
    }
}

fn opened() -> DebugConsole {
    DebugConsole {
        open: true,
        has_more: true,
        ..Default::default()
    }
}

#[test]
fn decode_turns_escapes_into_newlines_and_chars() {
    assert_eq!(decode_log_message("a\\nb"), "a\nb");
    assert_eq!(decode_log_message("a\\\\b"), "a\\b");
    // An escaped backslash before an n is literal, not a newline.
    assert_eq!(decode_log_message("a\\\\nb"), "a\\nb");
    assert_eq!(decode_log_message("a\\tb"), "atb");
    assert_eq!(decode_log_message("trailing\\"), "trailing\\");
    assert_eq!(decode_log_message("plain"), "plain");
}

#[test]
fn timestamps_parse_rfc3339_regardless_of_zone() {
    assert_eq!(timestamp_ms("2026-01-02T03:04:05Z"), 1_767_323_045_000);
    assert_eq!(timestamp_ms("2026-01-02T05:04:05+02:00"), 1_767_323_045_000);
    assert_eq!(timestamp_ms("not a timestamp"), 0);
}

#[test]
fn timestamps_parse_fractional_seconds_and_fall_back_without_a_zone() {
    // The server writes microsecond stamps (log_reader.py's isoformat).
    assert_eq!(
        timestamp_ms("2026-01-02T03:04:05.529718+00:00"),
        1_767_323_045_529
    );
    assert_eq!(timestamp_ms("2026-01-02T03:04:05.5Z"), 1_767_323_045_500);
    // Offset-less stamps are not RFC 3339; they fall back to the epoch.
    assert_eq!(timestamp_ms("2026-01-02T03:04:05"), 0);
}

#[test]
fn levels_choose_their_theme_color() {
    assert_eq!(level_style("INFO").fg, Some(theme::secondary()));
    assert_eq!(level_style("WARNING").fg, Some(theme::warning()));
    assert_eq!(level_style("ERROR").fg, Some(theme::error()));
    assert!(!level_style("ERROR").add_modifier.contains(Modifier::BOLD));
    let critical = level_style("CRITICAL");
    assert_eq!(critical.fg, Some(theme::error()));
    assert!(critical.add_modifier.contains(Modifier::BOLD));
    assert_eq!(level_style("DEBUG").fg, Some(theme::foreground()));
    assert_eq!(level_style("UNKNOWN").fg, Some(theme::foreground()));
}

#[test]
fn plain_lines_pad_the_level_to_eight() {
    assert_eq!(
        entry("a", "INFO", "hello").plain_line(),
        "2026-01-02 03:04:05 INFO     hello"
    );
    assert_eq!(
        entry("a", "CRITICAL", "hello").plain_line(),
        "2026-01-02 03:04:05 CRITICAL hello"
    );
}

#[test]
fn pages_parse_newest_first_with_cursor_and_has_more() {
    let value = json!({
        "logs": {
            "entries": [
                {"id": "b", "timestamp": "2026-01-02T03:04:05Z", "ppid": 1, "pid": 2,
                 "level": "WARNING", "message": "two\\nlines", "rawLine": "raw"},
                {"id": "a", "timestamp": "2026-01-02T03:04:04Z", "ppid": 1, "pid": 2,
                 "level": "INFO", "message": "one", "rawLine": "raw"}
            ],
            "hasMore": true,
            "cursor": 2
        }
    });
    let parsed = parse_page(&value).expect("a well-formed page");
    let ids: Vec<&str> = parsed
        .entries
        .iter()
        .map(|entry| entry.id.as_str())
        .collect();
    assert_eq!(ids, ["b", "a"]);
    assert_eq!(parsed.entries[0].message, "two\nlines");
    assert_eq!(parsed.entries[0].level, "WARNING");
    assert!(parsed.has_more);
    assert_eq!(parsed.cursor, Some(2));
    assert!(parse_page(&json!({"logs": {"hasMore": false}})).is_none());
    let exhausted = parse_page(&json!({"logs": {"entries": [], "hasMore": false, "cursor": null}}));
    let exhausted = exhausted.expect("an empty page is still a page");
    assert!(!exhausted.has_more);
    assert_eq!(exhausted.cursor, None);
}

#[test]
fn a_first_page_prepends_oldest_first_and_stops_at_the_end() {
    let mut console = opened();
    let keep_filling = apply_page(&mut console, page(&["b", "a"], false, None), 0);
    let ids: Vec<&str> = console
        .entries
        .iter()
        .map(|entry| entry.id.as_str())
        .collect();
    assert_eq!(ids, ["a", "b"]);
    assert!(!keep_filling);
    assert!(!console.has_more);
    assert_eq!(console.cursor, None);
}

#[test]
fn the_fill_loop_loads_while_the_viewport_is_not_full() {
    let mut console = opened();
    console.wrap_width = 80; // one visual line per entry
    console.body_height = 5;
    // An undrawn panel has no viewport height yet, so the fill continues
    // until a draw publishes one.
    let mut undrawn = opened();
    assert!(apply_page(
        &mut undrawn,
        page(&["a", "b"], true, Some(2)),
        0
    ));
    // Two entries fit five rows: keep loading older pages.
    assert!(apply_page(
        &mut console,
        page(&["a", "b"], true, Some(2)),
        0
    ));
    assert_eq!(console.cursor, Some(2));
    // Six entries overflow five rows: the viewport is full, stop filling.
    assert!(!apply_page(
        &mut console,
        page(&["f", "e", "d", "c"], true, Some(6)),
        2
    ));
    assert_eq!(total_visual(&console), 6);
}

#[test]
fn a_stale_first_page_does_not_rewind_the_poll_cursor() {
    // After a session switch the fill refetches from offset 0 while a poll
    // has already advanced the live offset; the page's own cursor must not
    // rewind it, or Home/Up walks already-loaded rows again.
    let mut console = opened();
    console.body_height = 1;
    debug_console::clear_session(&mut console);
    apply_polled(
        &mut console,
        &[
            entry("a", "INFO", "one"),
            entry("b", "INFO", "two"),
            entry("c", "INFO", "three"),
        ],
    );
    assert_eq!(console.cursor, Some(3));
    // The refetched first page duplicates the polled entries and reports a
    // cursor behind the live offset.
    assert!(!apply_page(
        &mut console,
        page(&["c", "b", "a"], true, Some(2)),
        0
    ));
    assert_eq!(console.cursor, Some(3));
    assert_eq!(console.entries.len(), 3);
}

#[test]
fn a_page_that_stops_advancing_the_cursor_ends_the_fill_loop() {
    let mut console = opened();
    console.body_height = 50;
    assert!(apply_page(
        &mut console,
        page(&["a", "b"], true, Some(2)),
        0
    ));
    // The same canned page answered again: the cursor does not advance, so the
    // loop stops even though every entry was already seen.
    assert!(!apply_page(
        &mut console,
        page(&["a", "b"], true, Some(2)),
        2
    ));
    assert_eq!(console.entries.len(), 2);
}

#[test]
fn a_poll_during_the_fill_does_not_stall_the_next_page() {
    // A poll that lands between two page requests marks the next page's
    // entries as seen; the cursor still advances, so the fill continues.
    let mut console = opened();
    console.body_height = 50;
    assert!(apply_page(
        &mut console,
        page(&["b", "a"], true, Some(2)),
        0
    ));
    let added = apply_polled(
        &mut console,
        &[entry("d", "INFO", "four"), entry("c", "INFO", "three")],
    );
    assert_eq!(added, 2);
    assert_eq!(console.cursor, Some(4));
    // The page at offset 2 adds nothing new, but its cursor progress keeps the fill going.
    assert!(apply_page(
        &mut console,
        page(&["d", "c"], true, Some(4)),
        2
    ));
    assert_eq!(console.entries.len(), 4);
}

#[test]
fn stale_answers_are_dropped_but_still_release_the_in_flight_slot() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.debug_console.open = true;
    app.debug_console
        .page_in_flight
        .store(true, Ordering::Relaxed);
    let current = app.debug_console.generation.load(Ordering::Relaxed);

    // A poll answer from before a toggle: dropped, entries untouched.
    apply_event(
        &mut app,
        &client,
        Event::Polled(current + 1, vec![entry("x", "INFO", "late")]),
    );
    assert!(app.debug_console.entries.is_empty());

    // A page answer from before a toggle: dropped, but the request finished,
    // so the in-flight slot is released for the next page.
    apply_event(
        &mut app,
        &client,
        Event::Page {
            generation: current + 1,
            session: "session".to_owned(),
            requested: 0,
            page: Some(page(&["x"], true, Some(1))),
        },
    );
    assert!(app.debug_console.entries.is_empty());
    assert!(!app.debug_console.page_in_flight.load(Ordering::Relaxed));
    assert_eq!(app.debug_console.cursor, None);
}

#[tokio::test]
async fn a_stale_poll_after_a_reopen_retries_the_first_page() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (tx, _rx) = mpsc::channel::<debug_console::Event>(8);
    let mut app = App::default();
    app.debug_console.open = true;
    app.debug_console.tx = Some(tx);
    app.set_session_id("session".to_owned());
    let current = app.debug_console.generation.load(Ordering::Relaxed);
    // A poll answer from before a close/reopen: dropped, but the reopened
    // console re-drives its first page (the Page arm does the same).
    apply_event(
        &mut app,
        &client,
        Event::Polled(current + 1, vec![entry("x", "INFO", "late")]),
    );
    assert!(app.debug_console.entries.is_empty());
    let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("the first page is requested")
        .expect("the stub answers");
    assert_eq!(frame["method"], "diagnostics/logs/read");
    assert_eq!(frame["params"]["limit"], PAGE_SIZE);
    assert_eq!(frame["params"]["offset"], 0);
    assert_eq!(frame["params"]["sessionId"], "session");
}

#[tokio::test]
async fn a_stale_page_from_the_previous_session_is_dropped_and_refetched() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (tx, _rx) = mpsc::channel::<debug_console::Event>(8);
    let mut app = App::default();
    app.debug_console.open = true;
    app.debug_console.tx = Some(tx);
    app.set_session_id("old".to_owned());
    let current = app.debug_console.generation.load(Ordering::Relaxed);
    // The session switched while the old session's page was in flight.
    app.set_session_id("new".to_owned());
    apply_event(
        &mut app,
        &client,
        Event::Page {
            generation: current,
            session: "old".to_owned(),
            requested: 0,
            page: Some(page(&["x"], true, Some(1))),
        },
    );
    assert!(app.debug_console.entries.is_empty());
    let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("the new session's page is requested")
        .expect("the stub answers");
    assert_eq!(frame["params"]["sessionId"], "new");
    assert_eq!(frame["params"]["offset"], 0);
}

#[test]
fn a_page_landing_at_the_top_does_not_snap_to_the_bottom() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.debug_console.open = true;
    app.set_session_id("session".to_owned());
    // An overfilled console scrolled to its top: twenty one-line entries in
    // a 5-row body, so the offset from the bottom is 15.
    app.debug_console.entries = (0..20)
        .map(|index| entry(&format!("e{index}"), "INFO", "short"))
        .collect();
    app.debug_console.wrap_counts = vec![1; 20];
    app.debug_console.body_height = 5;
    app.debug_console.has_more = true;
    app.debug_console.cursor = Some(20);
    app.debug_console.scroll = 15;
    assert!(at_top(&app.debug_console));
    let current = app.debug_console.generation.load(Ordering::Relaxed);
    // Reaching the top loads the previous page.
    apply_event(
        &mut app,
        &client,
        Event::Page {
            generation: current,
            session: "session".to_owned(),
            requested: 20,
            page: Some(page(&["old1", "old0"], true, Some(22))),
        },
    );
    // The older entries land above; the viewport keeps its content (the
    // offset from the bottom is unchanged) instead of cycling to the bottom.
    assert_eq!(app.debug_console.scroll, 15);
    assert_eq!(view(&app.debug_console).position, 2);
    let ids: Vec<&str> = app
        .debug_console
        .entries
        .iter()
        .map(|entry| entry.id.as_str())
        .collect();
    assert_eq!(&ids[..3], ["old0", "old1", "e0"]);
}

#[test]
fn a_fresh_page_answer_fills_and_a_fresh_poll_appends() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.debug_console.open = true;
    app.set_session_id("session".to_owned());
    app.debug_console
        .page_in_flight
        .store(true, Ordering::Relaxed);
    let current = app.debug_console.generation.load(Ordering::Relaxed);

    apply_event(
        &mut app,
        &client,
        Event::Page {
            generation: current,
            session: "session".to_owned(),
            requested: 0,
            page: Some(page(&["b", "a"], false, None)),
        },
    );
    assert!(!app.debug_console.page_in_flight.load(Ordering::Relaxed));
    let ids: Vec<&str> = app
        .debug_console
        .entries
        .iter()
        .map(|entry| entry.id.as_str())
        .collect();
    assert_eq!(ids, ["a", "b"]);

    apply_event(
        &mut app,
        &client,
        Event::Polled(current, vec![entry("c", "INFO", "three")]),
    );
    assert_eq!(app.debug_console.entries.len(), 3);
}

#[test]
fn polls_append_only_new_entries_and_advance_the_cursor() {
    let mut console = opened();
    apply_page(&mut console, page(&["b", "a"], true, Some(2)), 0);
    // The poll overlaps the newest entry; only "c" is new.
    let added = apply_polled(
        &mut console,
        &[entry("c", "ERROR", "three"), entry("b", "INFO", "two")],
    );
    assert_eq!(added, 1);
    let ids: Vec<&str> = console
        .entries
        .iter()
        .map(|entry| entry.id.as_str())
        .collect();
    assert_eq!(ids, ["a", "b", "c"]);
    assert_eq!(console.cursor, Some(3));
    // A poll with no session cursor (the log end) leaves it unset.
    let mut at_end = opened();
    apply_page(&mut at_end, page(&["a"], false, None), 0);
    apply_polled(&mut at_end, &[entry("b", "INFO", "two")]);
    assert_eq!(at_end.cursor, None);
    assert_eq!(at_end.entries.len(), 2);
}

#[test]
fn prepending_an_older_page_keeps_the_viewport_anchored() {
    let mut console = opened();
    console.body_height = 4;
    apply_page(&mut console, page(&["b", "a"], false, None), 0);
    console.scroll = 2; // scrolled up from the bottom
    apply_page(&mut console, page(&["d", "c"], true, Some(4)), 0);
    // The prepend lifts content below the viewport; the offset from the bottom is unchanged.
    assert_eq!(console.scroll, 2);
    let ids: Vec<&str> = console
        .entries
        .iter()
        .map(|entry| entry.id.as_str())
        .collect();
    assert_eq!(ids, ["c", "d", "a", "b"]);
}

#[test]
fn appending_below_a_scrolled_up_view_keeps_its_top_line() {
    let mut console = opened();
    console.wrap_width = 20; // long messages wrap to several visual lines
    apply_page(&mut console, page(&["a", "b", "c"], false, None), 0);
    console.scroll = 3;
    let added = apply_polled(&mut console, &[entry("d", "INFO", "message d")]);
    assert!(added > 1);
    assert_eq!(console.scroll, 3 + added as u16);
    // At the bottom the view follows the newest entry instead.
    console.scroll = 0;
    apply_polled(&mut console, &[entry("e", "INFO", "message e")]);
    assert_eq!(console.scroll, 0);
}

/// A console filled to the entry bound by one poll (newest first on the wire).
fn bounded_console() -> DebugConsole {
    let mut console = opened();
    console.wrap_width = 80; // one visual line per entry
    console.cursor = Some(0);
    let entries: Vec<LogEntry> = (0..MAX_ENTRIES)
        .rev()
        .map(|id| entry(&format!("e{id}"), "INFO", "message"))
        .collect();
    apply_polled(&mut console, &entries);
    console
}

#[test]
fn polls_past_the_entry_bound_drop_the_oldest_entries() {
    let mut console = bounded_console();
    apply_polled(&mut console, &[entry("newest", "INFO", "message")]);
    assert_eq!(console.entries.len(), MAX_ENTRIES);
    assert_eq!(console.entries[0].id, "e1"); // "e0" dropped
    assert_eq!(console.wrap_counts.len(), MAX_ENTRIES);
    assert!(!console.seen.contains("e0"));
    assert!(console.seen.contains("newest"));
    // The cursor steps back with the dropped entry: still the retained count.
    assert_eq!(console.cursor, Some(MAX_ENTRIES as u64));
}

#[test]
fn polls_past_the_visual_line_bound_drop_the_oldest_entries() {
    let mut console = opened();
    console.wrap_width = 20;
    // Three entries whose wrapped-line total passes the u16 viewport cap.
    console.entries = ["e0", "e1", "e2"]
        .iter()
        .map(|id| entry(id, "INFO", "message"))
        .collect();
    for id in ["e0", "e1", "e2"] {
        console.seen.insert(id.to_owned());
    }
    console.wrap_counts = vec![30_000, 30_000, 30_000];
    console.cursor = Some(3);
    apply_polled(&mut console, &[entry("new", "INFO", "message")]);
    assert_eq!(console.entries[0].id, "e1");
    assert_eq!(console.entries.last().unwrap().id, "new");
    assert!(total_visual(&console) <= MAX_VISUAL_LINES);
    assert!(!console.seen.contains("e0"));
    // The cursor steps back with the dropped entry: the retained count.
    assert_eq!(console.cursor, Some(3));
}

#[test]
fn trimming_keeps_a_scrolled_up_viewport_on_its_content() {
    let mut console = bounded_console();
    console.body_height = 10;
    console.scroll = 100;
    let top_entry = console.entries[view(&console).position as usize].id.clone();
    // Two new lines arrive and the two oldest drop: the total is unchanged
    // and the viewport keeps its top entry.
    apply_polled(
        &mut console,
        &[
            entry("n1", "INFO", "message"),
            entry("n0", "INFO", "message"),
        ],
    );
    assert_eq!(console.entries.len(), MAX_ENTRIES);
    assert_eq!(total_visual(&console), MAX_ENTRIES);
    assert_eq!(
        console.entries[view(&console).position as usize].id,
        top_entry
    );
}

#[test]
fn the_fill_loop_stops_at_the_entry_bound() {
    let mut console = bounded_console();
    // A hidden panel never publishes a viewport height, so without the
    // bound the loop would refetch the same page trim keeps dropping.
    assert!(!apply_page(
        &mut console,
        page(&["older"], true, Some(MAX_ENTRIES as u64 + 1)),
        MAX_ENTRIES as u64
    ));
}

#[test]
fn a_poll_does_not_rekick_paging_at_the_entry_bound() {
    let client = Arc::new(Client::stub());
    let (tx, _rx) = mpsc::channel::<debug_console::Event>(8);
    let mut app = App::default();
    app.debug_console.open = true;
    app.debug_console.tx = Some(tx);
    app.set_session_id("session".to_owned());
    let console = bounded_console();
    app.debug_console.entries = console.entries;
    app.debug_console.wrap_counts = console.wrap_counts;
    app.debug_console.seen = console.seen;
    app.debug_console.cursor = console.cursor;
    app.debug_console.has_more = true;
    let current = app.debug_console.generation.load(Ordering::Relaxed);
    apply_event(&mut app, &client, Event::Polled(current, vec![]));
    assert!(!app.debug_console.page_in_flight.load(Ordering::Relaxed));
}

#[test]
fn trimming_evicts_seen_ids_so_a_dropped_entry_can_return() {
    let mut console = bounded_console();
    apply_polled(&mut console, &[entry("newest", "INFO", "message")]);
    assert!(!console.seen.contains("e0"));
    // The dropped entry comes back in a later answer (a page re-fetching the
    // dropped range): the evicted id lets it through the dedupe filter.
    apply_polled(&mut console, &[entry("e0", "INFO", "message")]);
    assert_eq!(console.entries.len(), MAX_ENTRIES);
    assert_eq!(console.entries.last().unwrap().id, "e0");
    // The trim moved one further entry out to stay at the bound.
    assert_eq!(console.entries[0].id, "e2");
    assert!(console.seen.contains("e0"));
    assert!(!console.seen.contains("e1"));
    assert_eq!(console.cursor, Some(MAX_ENTRIES as u64));
}

#[test]
fn prepending_past_the_entry_bound_drops_the_oldest_entries() {
    let mut console = bounded_console();
    // Paging older at the bound prepends the page and drops it again: the
    // bound is the oldest history the console can show.
    apply_page(
        &mut console,
        page(&["old1", "old0"], true, Some(MAX_ENTRIES as u64 + 2)),
        MAX_ENTRIES as u64,
    );
    assert_eq!(console.entries.len(), MAX_ENTRIES);
    assert_eq!(console.entries[0].id, "e0");
    assert!(!console.seen.contains("old0"));
    assert_eq!(console.cursor, Some(MAX_ENTRIES as u64));
}

#[test]
fn the_view_resolves_top_and_bottom() {
    let mut console = opened();
    console.wrap_counts = vec![1; 10];
    console.body_height = 4;
    assert_eq!(view(&console).position, 6); // pinned to the bottom
    assert!(!at_top(&console));
    console.scroll = 6;
    assert_eq!(view(&console).position, 0);
    assert!(at_top(&console));
    // Content shorter than the viewport has no scroll room and sits at the top.
    console.wrap_counts = vec![1; 2];
    console.scroll = 0;
    assert!(at_top(&console));
}

#[test]
fn plain_navigation_keys_scroll_the_console_but_printable_keys_fall_through() {
    let plain = |code| KeyEvent::new(code, KeyModifiers::NONE);
    assert!(matches!(
        scroll_for_key(&plain(KeyCode::Up)),
        Some(Scroll::Up)
    ));
    assert!(matches!(
        scroll_for_key(&plain(KeyCode::Down)),
        Some(Scroll::Down)
    ));
    assert!(matches!(
        scroll_for_key(&plain(KeyCode::PageUp)),
        Some(Scroll::PageUp)
    ));
    assert!(matches!(
        scroll_for_key(&plain(KeyCode::PageDown)),
        Some(Scroll::PageDown)
    ));
    assert!(matches!(
        scroll_for_key(&plain(KeyCode::Home)),
        Some(Scroll::Home)
    ));
    assert!(scroll_for_key(&plain(KeyCode::End)).is_none());
    assert!(scroll_for_key(&plain(KeyCode::Char('a'))).is_none());
    let shifted = KeyEvent::new(KeyCode::Up, KeyModifiers::SHIFT);
    assert!(scroll_for_key(&shifted).is_none());
}

#[test]
fn scroll_home_canonicalizes_without_a_render() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.debug_console.open = true;
    app.debug_console.wrap_counts = vec![1; 10];
    app.debug_console.body_height = 4;
    scroll_home(&mut app, &client);
    // Clamped to the top immediately, not left at u16::MAX for the render to
    // fix: a degenerate panel that never draws must not strand the offset.
    assert_eq!(app.debug_console.scroll, 6);
    scroll_down(&mut app, 1);
    assert_eq!(app.debug_console.scroll, 5);
}

/// An app with the console open, visible, and one log entry loaded.
fn console_app() -> App {
    let mut app = App::default();
    app.debug_console.open = true;
    app.debug_console.visible = true;
    app.debug_console.entries = vec![entry("a", "INFO", "one")];
    app.debug_console.wrap_counts = vec![1];
    app.debug_console.body_height = 1;
    app
}

#[tokio::test]
async fn ctrl_backslash_toggles_the_debug_console() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.set_session_id("session".to_owned());
    // A legacy terminal sends Ctrl+\ as byte 0x1c, which crossterm decodes as
    // Ctrl+'4'; the Kitty protocol reports the backslash itself.
    let forms = [
        KeyEvent::new(KeyCode::Char('\\'), KeyModifiers::CONTROL),
        KeyEvent::new(KeyCode::Char('4'), KeyModifiers::CONTROL),
    ];
    for ctrl_backslash in forms {
        assert_eq!(
            input::handle_priority_key(&mut app, &client, ctrl_backslash),
            Some(false)
        );
        assert!(app.debug_console.open);
        assert_eq!(
            input::handle_priority_key(&mut app, &client, ctrl_backslash),
            Some(false)
        );
        assert!(!app.debug_console.open);
    }
}

#[tokio::test]
async fn the_debug_command_toggles_the_console() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = App::default();
    app.set_session_id("session".to_owned());
    // Slash commands are deferred while the session is starting.
    app.set_status(vibe_rs::app::Status::Ready);
    app.chat_input.load_full_text("/debug".to_owned());
    assert!(!submission::submit(&mut app, &client, &config_tx));
    assert!(app.debug_console.open);
    app.chat_input.load_full_text("/debug".to_owned());
    assert!(!submission::submit(&mut app, &client, &config_tx));
    assert!(!app.debug_console.open);
}

#[tokio::test]
async fn polls_follow_session_changes_while_the_console_is_open() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let mut app = App::default();
    let (tx, mut rx) = mpsc::channel::<debug_console::Event>(8);
    app.debug_console.tx = Some(tx);
    app.set_session_id("old".to_owned());
    debug_console::toggle(&mut app, &client);
    assert!(app.debug_console.open);
    assert!(app.debug_console.session_tx.is_some());
    // `/clear`, `/compact`, `/rewind` and `/resume` all switch the session id.
    app.set_session_id("new".to_owned());
    // The poll task reads the current id on every tick. Drive the answer
    // channel like the event loop does (applying a page answer releases the
    // in-flight slot a poll skips while it is held) and collect two polls.
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    let mut polls = 0;
    while polls < 2 && std::time::Instant::now() < deadline {
        tokio::select! {
            frame = frames.recv() => {
                let Some(frame) = frame else { break };
                // Polls read the newest entries; pages carry a page size and an offset.
                if frame["method"] == "diagnostics/logs/read" && frame["params"]["limit"] == 500 {
                    assert_eq!(frame["params"]["sessionId"], "new");
                    polls += 1;
                }
            }
            event = rx.recv() => {
                let Some(event) = event else { break };
                apply_event(&mut app, &client, event);
            }
        }
    }
    assert!(polls >= 1);
    // Closing drops the watch, so a later session change sends nowhere.
    debug_console::toggle(&mut app, &client);
    assert!(app.debug_console.session_tx.is_none());
}

#[test]
fn a_session_switch_clears_the_consoles_log_state() {
    let mut app = App::default();
    app.set_session_id("old".to_owned());
    let (session_tx, session_rx) = tokio::sync::watch::channel("old".to_owned());
    app.debug_console.session_tx = Some(session_tx);
    app.debug_console.open = true;
    apply_page(&mut app.debug_console, page(&["b", "a"], true, Some(2)), 0);
    app.debug_console.scroll = 1;
    // `/clear`, `/compact`, `/rewind` and `/resume` all switch the session id.
    app.set_session_id("new".to_owned());
    let console = &app.debug_console;
    assert!(console.entries.is_empty());
    assert!(console.wrap_counts.is_empty());
    assert!(console.seen.is_empty());
    assert!(console.hitmap.is_empty());
    assert_eq!(console.cursor, Some(0));
    assert!(console.has_more);
    assert_eq!(console.scroll, 0);
    // The panel, its poll task and the session watch survive the switch.
    assert!(console.open);
    assert!(console.session_tx.is_some());
    assert_eq!(*session_rx.borrow(), "new");
    // A same-id update keeps the loaded log.
    apply_polled(&mut app.debug_console, &[entry("c", "INFO", "three")]);
    app.set_session_id("new".to_owned());
    assert_eq!(app.debug_console.entries.len(), 1);
}

#[tokio::test]
async fn a_filling_poll_after_a_session_switch_keeps_older_history_reachable() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (tx, _rx) = mpsc::channel::<debug_console::Event>(8);
    let mut app = App::default();
    app.debug_console.open = true;
    app.debug_console.tx = Some(tx);
    app.set_session_id("old".to_owned());
    app.set_session_id("new".to_owned());
    let current = app.debug_console.generation.load(Ordering::Relaxed);
    // The first poll of the new session delivers more entries than the
    // viewport holds, so the fill loop never kicks a first page: only the
    // poll can establish the cursor.
    app.debug_console.wrap_width = 20;
    app.debug_console.body_height = 2;
    let polled: Vec<LogEntry> = (0..3)
        .map(|index| entry(&format!("n{index}"), "INFO", "message"))
        .collect();
    apply_event(&mut app, &client, Event::Polled(current, polled));
    assert_eq!(app.debug_console.cursor, Some(3));
    // Scrolling to the top pages the new session's older history from the
    // poll-established offset instead of refusing on a missing cursor.
    debug_console::scroll_home(&mut app, &client);
    assert!(at_top(&app.debug_console));
    let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("the older page is requested")
        .expect("the stub answers");
    assert_eq!(frame["params"]["sessionId"], "new");
    assert_eq!(frame["params"]["offset"], 3);
    assert_eq!(frame["params"]["limit"], PAGE_SIZE);
}

#[tokio::test]
async fn navigation_keys_scroll_the_console_while_it_is_open() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = console_app();
    let up = KeyEvent::new(KeyCode::Up, KeyModifiers::NONE);
    assert!(!input::handle_key(&mut app, &client, &config_tx, up));
    assert_eq!(app.debug_console.scroll, 1);
    // Printable keys still reach the composer.
    let letter = KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE);
    assert!(!input::handle_key(&mut app, &client, &config_tx, letter));
    assert_eq!(app.chat_input.input, "a");
    assert_eq!(app.debug_console.scroll, 1);
}

#[test]
fn the_panel_docks_only_where_the_main_area_stays_usable() {
    let app = console_app();
    let narrow = Rect::new(0, 0, 79, 10);
    assert!(vibe_rs::ui::debug_console::split(&app, narrow).1.is_none());
    let wide = Rect::new(0, 0, 120, 10);
    let (main, panel) = vibe_rs::ui::debug_console::split(&app, wide);
    let panel = panel.expect("the panel docks on a wide screen");
    assert_eq!(main.width, 72);
    assert_eq!(panel.x, main.right());
    assert_eq!(panel.width, 48);
    // The narrowest docking screen still leaves the chat area 40 columns.
    let edge = Rect::new(0, 0, 80, 10);
    let (main, _) = vibe_rs::ui::debug_console::split(&app, edge);
    assert_eq!(main.width, 40);
    // A screen too short for the header and one log line hides the panel too.
    let short = Rect::new(0, 0, 120, 2);
    assert!(vibe_rs::ui::debug_console::split(&app, short).1.is_none());
    let shortest = Rect::new(0, 0, 120, 3);
    assert!(vibe_rs::ui::debug_console::split(&app, shortest)
        .1
        .is_some());
}

#[tokio::test]
async fn a_hidden_console_does_not_take_the_navigation_keys() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = console_app();
    // A terminal too narrow for the dock hides the panel while it stays open.
    app.debug_console.visible = false;
    // A two-line prompt: Up moves the composer's caret up a row.
    app.chat_input.input = "first\nsecond".into();
    app.chat_input.cursor = app.chat_input.input.len();
    let up = KeyEvent::new(KeyCode::Up, KeyModifiers::NONE);
    assert!(!input::handle_key(&mut app, &client, &config_tx, up));
    assert_eq!(app.debug_console.scroll, 0);
    assert!(app.chat_input.cursor < app.chat_input.input.len());
}

#[tokio::test]
async fn the_queue_selection_mode_keeps_the_navigation_keys() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = console_app();
    app.queue.selected = Some("queued".to_owned());
    let up = KeyEvent::new(KeyCode::Up, KeyModifiers::NONE);
    assert!(!input::handle_key(&mut app, &client, &config_tx, up));
    assert_eq!(app.debug_console.scroll, 0);
}

#[tokio::test]
async fn queue_editing_keeps_the_navigation_keys() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = console_app();
    app.queue.selected = Some("queued".to_owned());
    app.queue.editing = true;
    // A two-line prompt: Up moves the composer's caret up a row.
    app.chat_input.input = "first\nsecond".into();
    app.chat_input.cursor = app.chat_input.input.len();
    let up = KeyEvent::new(KeyCode::Up, KeyModifiers::NONE);
    assert!(!input::handle_key(&mut app, &client, &config_tx, up));
    assert_eq!(app.debug_console.scroll, 0);
    assert!(app.chat_input.cursor < app.chat_input.input.len());
}

#[tokio::test]
async fn an_open_completion_popup_keeps_the_navigation_keys() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = console_app();
    app.completion.entries = vec![CompletionEntry {
        label: "/debug".to_owned(),
        description: String::new(),
        origin: None,
    }];
    let up = KeyEvent::new(KeyCode::Up, KeyModifiers::NONE);
    assert!(!input::handle_key(&mut app, &client, &config_tx, up));
    assert_eq!(app.debug_console.scroll, 0);
}

#[test]
fn the_wheel_scrolls_the_console_log() {
    let client = Arc::new(Client::stub());
    let mut app = console_app();
    debug_console::wheel(&mut app, &client, true);
    assert_eq!(app.debug_console.scroll, 2); // one wheel notch
    debug_console::wheel(&mut app, &client, false);
    assert_eq!(app.debug_console.scroll, 0);
}

#[test]
fn the_hitmap_maps_body_rows_to_entries() {
    let mut console = opened();
    console.hitmap = vec![(3, 4, 0), (4, 5, 0), (5, 6, 1)];
    assert_eq!(entry_at(&console, 3), Some(0));
    assert_eq!(entry_at(&console, 4), Some(0));
    assert_eq!(entry_at(&console, 5), Some(1));
    assert_eq!(entry_at(&console, 6), None);
}

#[test]
fn rewrapping_keeps_the_viewport_top_line() {
    let mut app = App::default();
    app.debug_console.open = true;
    // 49-char lines: one visual row at the wide body's 53 columns, two at the
    // narrow body's 37.
    app.debug_console.entries = (0..20)
        .map(|index| entry(&format!("e{index}"), "INFO", &"x".repeat(20)))
        .collect();
    let mut terminal = Terminal::new(TestBackend::new(140, 14)).expect("terminal");
    terminal.draw(|frame| app.draw(frame)).expect("draw");
    assert_eq!(total_visual(&app.debug_console), 20);
    app.debug_console.scroll = 3;
    let anchor = view(&app.debug_console).position;
    assert_eq!(anchor, 5);
    // Narrowing and shortening the terminal re-wraps every entry to two rows
    // in an 8-row body; the viewport stays on the same top line.
    let mut narrow = Terminal::new(TestBackend::new(100, 10)).expect("terminal");
    narrow.draw(|frame| app.draw(frame)).expect("draw");
    assert_eq!(total_visual(&app.debug_console), 40);
    assert_eq!(view(&app.debug_console).position, anchor);
}

#[test]
fn the_console_paints_a_scrollbar_only_when_the_log_overflows() {
    let mut app = App::default();
    app.debug_console.open = true;
    app.debug_console.entries = (0..20)
        .map(|index| entry(&format!("e{index}"), "INFO", "short"))
        .collect();
    let mut terminal = Terminal::new(TestBackend::new(120, 14)).expect("terminal");
    terminal.draw(|frame| app.draw(frame)).expect("draw");
    // Twenty single-row entries overflow the 12-row body: the scrollbar's
    // track is registered and its thumb paints a reversed cell.
    let body = app.debug_console.body_area;
    assert!(!mouse::scrollbar_tracks(&app, body).is_empty());
    let buffer = terminal.backend().buffer();
    let thumb = (body.y..body.bottom()).any(|row| {
        buffer[(body.right() - 1, row)]
            .modifier
            .contains(Modifier::REVERSED)
    });
    assert!(thumb, "no scrollbar thumb in the body's right column");
    // A log that fits the body paints no scrollbar: no track, no thumb.
    app.debug_console.entries.truncate(2);
    app.debug_console.wrap_counts.truncate(2);
    terminal.draw(|frame| app.draw(frame)).expect("draw");
    let body = app.debug_console.body_area;
    assert!(mouse::scrollbar_tracks(&app, body).is_empty());
    let buffer = terminal.backend().buffer();
    let thumb = (body.y..body.bottom()).any(|row| {
        buffer[(body.right() - 1, row)]
            .modifier
            .contains(Modifier::REVERSED)
    });
    assert!(!thumb, "a fitting log still paints a scrollbar thumb");
}

/// An app with the console open, three log entries loaded, and the panel drawn.
fn drawn_console_app() -> (App, Terminal<TestBackend>) {
    let mut app = App::default();
    app.debug_console.open = true;
    app.debug_console.entries = (0..3)
        .map(|index| entry(&format!("e{index}"), "INFO", "short"))
        .collect();
    let mut terminal = Terminal::new(TestBackend::new(120, 14)).expect("terminal");
    terminal.draw(|frame| app.draw(frame)).expect("draw");
    (app, terminal)
}

#[test]
fn a_wheel_over_the_console_header_or_border_does_not_scroll_the_log() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let (mut app, _terminal) = drawn_console_app();
    let wheel_up = |column: u16, row: u16| MouseEvent {
        kind: MouseEventKind::ScrollUp,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    };
    // The log body scrolls.
    mouse::handle(&mut app, &client, &config_tx, wheel_up(80, 3));
    assert_eq!(app.debug_console.scroll, 2);
    // The header above it and the panel's border stay inert.
    mouse::handle(&mut app, &client, &config_tx, wheel_up(80, 0));
    mouse::handle(&mut app, &client, &config_tx, wheel_up(72, 3));
    assert_eq!(app.debug_console.scroll, 2);
}

#[test]
fn a_click_released_off_the_log_body_does_not_copy() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let (mut app, _terminal) = drawn_console_app();
    let click = |kind: MouseEventKind, column: u16, row: u16| MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    };
    let copied = |app: &App| {
        app.overlays
            .toasts
            .iter()
            .any(|toast| toast.text == "Copied to clipboard")
    };
    // A click on a log line copies it.
    mouse::handle(
        &mut app,
        &client,
        &config_tx,
        click(MouseEventKind::Down(MouseButton::Left), 80, 3),
    );
    mouse::handle(
        &mut app,
        &client,
        &config_tx,
        click(MouseEventKind::Up(MouseButton::Left), 80, 3),
    );
    assert!(copied(&app));
    app.overlays.toasts.clear();
    // The router keeps the pressed target wherever the pointer is released,
    // but a release off the body (the border, or outside the panel) copies
    // nothing.
    mouse::handle(
        &mut app,
        &client,
        &config_tx,
        click(MouseEventKind::Down(MouseButton::Left), 80, 3),
    );
    mouse::handle(
        &mut app,
        &client,
        &config_tx,
        click(MouseEventKind::Up(MouseButton::Left), 72, 3),
    );
    assert!(!copied(&app));
    mouse::handle(
        &mut app,
        &client,
        &config_tx,
        click(MouseEventKind::Down(MouseButton::Left), 80, 3),
    );
    mouse::handle(
        &mut app,
        &client,
        &config_tx,
        click(MouseEventKind::Up(MouseButton::Left), 10, 3),
    );
    assert!(!copied(&app));
    // A release on the header's pad row above the body copies nothing either.
    mouse::handle(
        &mut app,
        &client,
        &config_tx,
        click(MouseEventKind::Down(MouseButton::Left), 80, 3),
    );
    mouse::handle(
        &mut app,
        &client,
        &config_tx,
        click(MouseEventKind::Up(MouseButton::Left), 80, 1),
    );
    assert!(!copied(&app));
}
