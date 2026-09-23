//! Toast text selection: its own region, click granularity, and its lifetime.

use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::Terminal;

use vibe_rs::app::{App, ToastSeverity};
use vibe_rs::mouse::{self, MouseTarget};
use vibe_rs::pointer::{shape, Shape};
use vibe_rs::selection;

const TOAST: &str = "Slash commands cannot be queued";
const TOAST_SECS: u64 = 60;
/// Column of "commands" within the toast text row.
const SECOND_WORD: u16 = 6;

/// Draw one 80x24 frame showing `TOAST`, leaving its painted regions published.
fn drawn() -> App {
    let mut app = App::default();
    app.show_toast(TOAST.to_owned(), ToastSeverity::Warning, TOAST_SECS);
    redraw(&mut app);
    app
}

fn redraw(app: &mut App) {
    let mut terminal = Terminal::new(TestBackend::new(80, 24)).expect("terminal");
    terminal.draw(|frame| app.draw(frame)).expect("draw");
}

/// Top-left cell of the newest toast's text, as the last frame painted it.
fn text_origin(app: &App) -> (u16, u16) {
    let (_, area) = *app
        .view
        .toast_text_areas
        .last()
        .expect("a painted toast text row");
    (area.x, area.y)
}

/// Drag from the first toast text cell through `columns`, as a mouse would.
fn drag_from_start(app: &mut App, columns: u16) {
    let (x, y) = text_origin(app);
    selection::press_toast(app, (x, y));
    selection::drag(app, (x + columns, y));
    selection::release(app);
    redraw(app);
}

/// Click `times` on the same toast cell, as a multi-click gesture would.
fn click(app: &mut App, column: u16, times: usize) {
    let (x, y) = text_origin(app);
    for _ in 0..times {
        selection::press_toast(app, (x + column, y));
        selection::release(app);
    }
    redraw(app);
}

fn selected_text(app: &App) -> Option<&str> {
    app.selection.region.as_ref().map(|sel| sel.text.as_str())
}

#[test]
fn the_toast_text_row_owns_the_pointer() {
    let app = drawn();

    assert_eq!(
        mouse::target_at(&app, text_origin(&app)),
        Some(MouseTarget::Toast)
    );
}

#[test]
fn hovering_toast_text_asks_for_the_beam() {
    let mut app = drawn();
    app.view.mouse_position = Some(text_origin(&app));

    assert_eq!(shape(&app), Shape::Text);
}

#[test]
fn dragging_toast_text_selects_it_without_dismissing_the_toast() {
    let mut app = drawn();

    drag_from_start(&mut app, 4);

    assert_eq!(selected_text(&app), Some("Slash"));
    assert_eq!(app.overlays.toasts.len(), 1);
}

#[test]
fn a_double_click_selects_the_toast_word_under_the_pointer() {
    let mut app = drawn();

    click(&mut app, SECOND_WORD, 2);

    assert_eq!(selected_text(&app), Some("commands"));
}

#[test]
fn a_triple_click_selects_the_whole_toast_row() {
    let mut app = drawn();

    click(&mut app, SECOND_WORD, 3);

    assert_eq!(selected_text(&app), Some(TOAST));
}

#[test]
fn a_fourth_click_wraps_the_chain_back_to_a_plain_click() {
    let mut app = drawn();

    click(&mut app, SECOND_WORD, 4);

    // Char granularity on one cell selects nothing, as on every other surface.
    assert_eq!(selected_text(&app), None);
}

#[test]
fn a_press_below_the_toast_leaves_its_text_unselected() {
    let mut app = drawn();
    let (x, y) = text_origin(&app);

    selection::press(&mut app, (x, y + 3));

    assert!(app.selection.region.is_none());
}

#[test]
fn the_newest_toast_of_a_stack_is_the_one_selected() {
    let mut app = drawn();
    app.show_toast("Second toast".to_owned(), ToastSeverity::Error, TOAST_SECS);
    redraw(&mut app);

    drag_from_start(&mut app, 5);

    assert_eq!(selected_text(&app), Some("Second"));
}

#[test]
fn a_dismissed_toast_drops_its_selection_and_region() {
    let mut app = drawn();
    drag_from_start(&mut app, 4);

    app.overlays.toasts.clear();
    redraw(&mut app);

    assert!(app.selection.region.is_none());
    assert_eq!(app.view.toast_selection_region.area, Rect::default());
}

#[test]
fn copying_toast_text_keeps_leading_transcript_chrome_glyphs() {
    let mut app = App::default();
    app.show_toast(
        "> /path failed".to_owned(),
        ToastSeverity::Warning,
        TOAST_SECS,
    );
    redraw(&mut app);

    drag_from_start(&mut app, 13);

    assert_eq!(selected_text(&app), Some("> /path failed"));
}

#[test]
fn a_selection_follows_its_toast_when_a_newer_one_shifts_the_rack() {
    let mut app = drawn();
    drag_from_start(&mut app, 4);
    let (_, before) = app.view.toast_text_areas[0];

    app.show_toast("Second toast".to_owned(), ToastSeverity::Error, TOAST_SECS);
    redraw(&mut app);
    let (_, after) = app.view.toast_text_areas[0];

    assert!(after.y < before.y, "the older toast moved up the rack");
    assert_eq!(selected_text(&app), Some("Slash"));
    assert_eq!(app.view.toast_selection_region.area, after);
}
