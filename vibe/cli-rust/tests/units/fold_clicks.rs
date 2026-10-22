//! Regression: rapid clicks on a foldable entry chained into word selection instead of toggling it.

use std::sync::Arc;

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use serde_json::json;

use vibe_rs::app::App;
use vibe_rs::mouse::{self, MouseTarget};
use vibe_rs::selection::Granularity;

/// Screen rows the reasoning entry occupies, and a column inside the transcript.
const ENTRY_TOP: u16 = 2;
const ENTRY_BOTTOM: u16 = 5;
const COLUMN: u16 = 5;
const ON_ENTRY: (u16, u16) = (COLUMN, ENTRY_TOP);
const ON_BODY: (u16, u16) = (COLUMN, ENTRY_TOP + 1);
const PLAIN_TEXT: (u16, u16) = (COLUMN, ENTRY_BOTTOM + 2);

/// A settled frame: one foldable reasoning entry above plain transcript rows.
fn transcript() -> App {
    let mut app = App::default();
    app.view.transcript.add(&json!({
        "entry": {"id": "r1", "type": "reasoning", "text": "why", "generationStatus": "completed"}
    }));
    app.view.selection_region.area = Rect::new(0, 0, 40, 10);
    mouse::register_region(&mut app, Rect::new(0, 0, 40, 10), MouseTarget::Transcript);
    app.view.entry_hitmap = vec![(ENTRY_TOP, ENTRY_BOTTOM, Some(ENTRY_TOP), "r1".to_owned())];
    app
}

fn send(app: &mut App, kind: MouseEventKind, at: (u16, u16)) {
    let client = Arc::new(vibe_rs::server::Client::stub());
    let (config_tx, _rx) = tokio::sync::mpsc::channel(1);
    let event = MouseEvent {
        kind,
        column: at.0,
        row: at.1,
        modifiers: KeyModifiers::NONE,
    };
    mouse::handle(app, &client, &config_tx, event);
}

fn click(app: &mut App, at: (u16, u16)) {
    send(app, MouseEventKind::Down(MouseButton::Left), at);
    send(app, MouseEventKind::Up(MouseButton::Left), at);
}

fn expanded(app: &App) -> bool {
    app.view.expanded.contains("r1")
}

#[test]
fn every_click_of_a_double_or_triple_click_toggles_the_entry() {
    let mut app = transcript();

    let states: Vec<bool> = (0..3)
        .map(|_| {
            click(&mut app, ON_ENTRY);
            expanded(&app)
        })
        .collect();

    assert_eq!(states, [true, false, true]);
    assert!(app.selection.region.is_none());
}

#[test]
fn a_drag_onto_the_next_cell_selects_both_cells_without_toggling() {
    let mut app = transcript();
    let next = (COLUMN + 1, ENTRY_TOP);

    send(&mut app, MouseEventKind::Down(MouseButton::Left), ON_ENTRY);
    send(&mut app, MouseEventKind::Drag(MouseButton::Left), next);
    send(&mut app, MouseEventKind::Up(MouseButton::Left), next);

    assert!(!expanded(&app));
    let selection = app.selection.region.as_ref().expect("a two-cell selection");
    assert_eq!((selection.anchor.0, selection.head.0), (ON_ENTRY.0, next.0));
    assert!(selection.pending_copy);
}

#[test]
fn a_double_click_on_plain_text_still_selects_a_word() {
    let mut app = transcript();

    click(&mut app, PLAIN_TEXT);
    click(&mut app, PLAIN_TEXT);

    assert_eq!(app.selection.granularity, Granularity::Word);
    assert!(app.selection.region.is_some());
}

#[test]
fn with_a_selection_showing_a_double_click_on_the_body_selects_a_word_and_keeps_it_open() {
    let mut app = transcript();
    app.view.expanded.insert("r1".to_owned());
    let drag_end = (COLUMN + 3, PLAIN_TEXT.1);
    send(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        PLAIN_TEXT,
    );
    send(&mut app, MouseEventKind::Drag(MouseButton::Left), drag_end);
    send(&mut app, MouseEventKind::Up(MouseButton::Left), drag_end);

    click(&mut app, ON_BODY);
    click(&mut app, ON_BODY);

    assert!(expanded(&app));
    assert_eq!(app.selection.granularity, Granularity::Word);
    assert!(app.selection.region.is_some());
}

#[test]
fn a_click_after_a_collapse_lands_as_a_single_click() {
    let mut app = transcript();
    app.view.expanded.insert("r1".to_owned());
    click(&mut app, ON_BODY);
    assert!(!expanded(&app));
    // Collapsed, the entry keeps only its header row: plain text now sits under the pointer.
    app.view.entry_hitmap = vec![(ENTRY_TOP, ENTRY_TOP + 1, Some(ENTRY_TOP), "r1".to_owned())];

    click(&mut app, ON_BODY);

    assert_eq!(app.selection.granularity, Granularity::Char);
    assert!(app.selection.region.is_none());
    assert!(!expanded(&app));
}
