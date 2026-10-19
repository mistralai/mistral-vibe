//! Option lists: highlight, viewport, hit map and the click rules.

use std::sync::Arc;

use ratatui::layout::Rect;
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::mcp::{self, rows, rows::Row};
use vibe_rs::option_list::{Click, MultiAction, OptionList, SingleAction};
use vibe_rs::server::Client;

fn click(kind: crossterm::event::MouseEventKind, row: u16) -> crossterm::event::MouseEvent {
    crossterm::event::MouseEvent {
        kind,
        column: 4,
        row,
        modifiers: crossterm::event::KeyModifiers::NONE,
    }
}

fn press_release(row: u16) -> [crossterm::event::MouseEvent; 2] {
    use crossterm::event::{MouseButton, MouseEventKind};
    [
        click(MouseEventKind::Down(MouseButton::Left), row),
        click(MouseEventKind::Up(MouseButton::Left), row),
    ]
}

/// One line per option, at screen rows `0..len`.
fn rows_list(len: usize, selected: usize) -> OptionList<MultiAction> {
    let mut list = OptionList::<MultiAction>::new(selected);
    list.layout_rows(len, Rect::new(0, 0, 80, len as u16));
    list
}

#[test]
fn a_click_highlights_and_a_click_on_the_highlight_confirms() {
    let mut list = rows_list(4, 0);

    list.press((4, 2));
    assert_eq!(list.release((4, 2)), Some(Click::Highlight(2)));
    assert_eq!(list.selected(), 2);
    list.press((4, 2));
    assert_eq!(list.release((4, 2)), Some(Click::Confirm(2)));
}

#[test]
fn a_single_action_list_confirms_on_one_click() {
    let mut list = OptionList::<SingleAction>::new(0);
    list.layout_rows(4, Rect::new(0, 0, 80, 4));

    list.press((4, 2));
    assert_eq!(list.release((4, 2)), Some(2));
    assert_eq!(list.selected(), 2);
    list.press((4, 2));
    assert_eq!(list.release((4, 3)), None);
}

#[test]
fn a_release_off_the_pressed_option_does_nothing() {
    let mut list = rows_list(4, 1);

    list.press((4, 1));
    assert_eq!(list.release((4, 2)), None);
    list.press((4, 1));
    assert_eq!(list.release((4, 9)), None);
    list.press((4, 1));
    list.cancel();
    assert_eq!(list.release((4, 1)), None);
    assert_eq!(list.selected(), 1);
}

#[test]
fn a_highlight_moved_during_the_press_only_highlights_on_release() {
    let mut list = rows_list(4, 0);

    list.press((4, 0));
    list.step(true, 4, false, |_| true);
    assert_eq!(list.release((4, 0)), Some(Click::Highlight(0)));

    list.press((4, 0));
    list.select(3);
    assert_eq!(list.release((4, 0)), Some(Click::Highlight(0)));

    list.press((4, 0));
    list.step(true, 4, false, |_| true);
    list.step(false, 4, false, |_| true);
    assert_eq!(list.release((4, 0)), Some(Click::Highlight(0)));

    list.press((4, 0));
    list.select(3);
    list.reset();
    assert_eq!(list.release((4, 0)), Some(Click::Highlight(0)));

    list.press((4, 0));
    list.highlight(2);
    list.highlight(0);
    assert_eq!(list.release((4, 0)), Some(Click::Highlight(0)));
}

#[test]
fn reapplying_the_same_highlight_keeps_a_pending_confirm() {
    let mut list = rows_list(4, 1);

    list.press((4, 1));
    list.highlight(1);
    list.select(1);
    assert_eq!(list.release((4, 1)), Some(Click::Confirm(1)));
}

#[test]
fn highlight_keeps_a_detached_viewport_and_select_reattaches_it() {
    let mut list = rows_list(4, 0);
    list.detach_at(2);

    list.highlight(3);
    assert!(list.free_scroll());
    assert_eq!(list.scroll(), 2);

    list.select(1);
    assert!(!list.free_scroll());

    list.detach_at(2);
    list.reset_viewport();
    assert!(!list.free_scroll());
    assert_eq!(list.scroll(), 0);
}

#[test]
fn step_skips_unselectable_options_and_stops_or_wraps_at_the_ends() {
    let mut list = OptionList::<MultiAction>::new(0);
    assert!(list.step(true, 4, false, |index| index != 1));
    assert_eq!(list.selected(), 2);
    list.highlight(3);
    assert!(!list.step(true, 4, false, |_| true));
    assert_eq!(list.selected(), 3);
    assert!(list.step(true, 4, true, |_| true));
    assert_eq!(list.selected(), 0);
    assert!(list.step(false, 4, true, |_| true));
    assert_eq!(list.selected(), 3);
}

#[test]
fn free_scroll_is_clamped_to_the_last_viewport() {
    let mut list = OptionList::<MultiAction>::new(0);
    list.detach_at(usize::MAX);

    assert_eq!(list.layout(&[None; 30], Rect::new(0, 0, 80, 10)), 20);
}

#[test]
fn layout_reveals_every_line_of_a_wrapped_option() {
    let mut lines = vec![None; 20];
    lines[2] = Some(0);
    lines[8..=10].fill(Some(1));
    lines[12] = Some(2);
    let mut list = OptionList::<MultiAction>::new(1);

    assert_eq!(list.layout(&lines, Rect::new(0, 0, 80, 5)), 6);
}

#[test]
fn an_oversized_option_anchors_at_its_first_line() {
    let mut lines = vec![None; 20];
    lines[5..=10].fill(Some(1));
    let mut list = OptionList::<MultiAction>::new(1);

    assert_eq!(list.layout(&lines, Rect::new(0, 0, 80, 3)), 5);
}

#[test]
fn hits_cover_only_the_visible_option_lines() {
    let mut lines = vec![None; 13];
    lines[1] = Some(0);
    lines[10..=11].fill(Some(1));
    lines[12] = Some(2);
    let mut list = OptionList::<MultiAction>::new(0);
    list.detach_at(10);
    list.layout(&lines, Rect::new(0, 21, 80, 2));

    assert_eq!(list.index_at((4, 21)), Some(1));
    assert_eq!(list.index_at((4, 22)), Some(1));
    assert_eq!(list.index_at((4, 20)), None);
    assert_eq!(list.index_at((4, 23)), None);
}

#[test]
fn approval_click_highlights_an_option_without_answering() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.approval.list.layout_rows(4, Rect::new(2, 10, 40, 4));

    for event in press_release(12) {
        vibe_rs::approval::handle_mouse(&mut app, &client, event);
    }
    assert_eq!(app.approval.list.selected(), 2);

    for event in press_release(14) {
        vibe_rs::approval::handle_mouse(&mut app, &client, event);
    }
    assert_eq!(app.approval.list.selected(), 2);
}

fn mcp_app() -> App {
    let mut app = App::default();
    app.mcp.open = true;
    let source = |name: &str| json!({"name": name, "kind": "server", "transport": "stdio", "status": "connected", "tools": []});
    mcp::apply_runtime(
        &mut app,
        &json!({"runtime": {"mcp": {"sources": [source("alpha"), source("beta")]}}}),
    );
    let rows = rows::rows(&app.mcp);
    let lines: Vec<_> = (0..rows.len())
        .map(|index| rows[index].selectable().then_some(index))
        .collect();
    app.mcp
        .list
        .layout(&lines, Rect::new(0, 0, 80, rows.len() as u16));
    app
}

#[test]
fn mcp_source_opens_on_the_second_click() {
    let mut app = mcp_app();
    let beta = rows::rows(&app.mcp)
        .iter()
        .position(|row| matches!(row, Row::Source(source) if source.name == "beta"))
        .unwrap() as u16;

    for event in press_release(beta) {
        mcp::mouse(&mut app, event);
    }
    assert_eq!(app.mcp.list.selected(), usize::from(beta));
    assert_eq!(app.mcp.viewing_name, None);

    for event in press_release(beta) {
        mcp::mouse(&mut app, event);
    }
    assert_eq!(app.mcp.viewing_name.as_deref(), Some("beta"));
}

#[test]
fn mcp_click_on_the_highlight_while_searching_leaves_search_and_opens_it() {
    let mut app = mcp_app();
    app.mcp.search.focused = true;
    let first = app.mcp.list.selected() as u16;

    for event in press_release(first) {
        mcp::mouse(&mut app, event);
    }
    assert!(!app.mcp.search.focused);
    assert_eq!(app.mcp.viewing_name.as_deref(), Some("alpha"));
}

#[test]
fn mcp_search_keeps_the_viewport_on_the_highlight() {
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
    let mut app = mcp_app();

    mcp::search::focus(&mut app);
    assert!(!app.mcp.list.free_scroll());

    mcp::search::handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('b'), KeyModifiers::NONE),
    );
    assert!(!app.mcp.list.free_scroll());
}
