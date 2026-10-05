//! Scrollbar track clicks page toward the pointer and repeat while held.

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;

use vibe_rs::{
    app::App,
    mouse,
    mouse::MouseTarget,
    ui::scrollbar::{Side, State},
};

const AREA: Rect = Rect::new(0, 0, 11, 10);
const BAR: Rect = Rect::new(10, 0, 1, 10);
const REPEAT_DELAY: usize = 6;

#[test]
fn click_above_thumb_pages_transcript_up() {
    let mut app = transcript(90);

    click(&mut app, 0);

    assert_eq!(app.view.scroll_target, 10);
    assert_eq!(app.view.scroll, 0);
    assert!(!app.view.mouse.is_dragging_scrollbar());
}

#[test]
fn click_below_thumb_pages_transcript_down() {
    let mut app = transcript(50);
    app.view.scroll = 40;
    app.view.scroll_target = 40;

    click(&mut app, 9);

    assert_eq!(app.view.scroll_target, 30);
}

#[test]
fn page_clamps_to_scroll_bounds() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 40, 30);

    click(&mut app, 0);
    assert_eq!(app.completion.scroll, 0);

    click(&mut app, 9);
    assert_eq!(app.completion.scroll, 60);
}

#[test]
fn track_click_scrolls_non_transcript_targets() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 10, 50);

    click(&mut app, 0);
    assert_eq!(app.completion.scroll, 40);
    assert!(!app.completion.reveal);

    click(&mut app, 9);
    assert_eq!(app.completion.scroll, 60);
}

#[test]
fn thumb_click_drags_instead_of_paging() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 10, 50);

    press(&mut app, 5);

    assert!(app.view.mouse.is_dragging_scrollbar());
    assert!(!mouse::is_track_paging(&app));
    assert_eq!(app.completion.scroll, 0);
}

#[test]
fn held_track_click_waits_before_repeating() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 10, 90);

    press(&mut app, 0);
    tick(&mut app, REPEAT_DELAY);
    assert_eq!(app.completion.scroll, 80);

    tick(&mut app, 1);
    assert_eq!(app.completion.scroll, 70);
}

#[test]
fn held_track_click_stops_when_thumb_reaches_pointer() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 10, 90);

    press(&mut app, 5);
    tick(&mut app, REPEAT_DELAY + 20);

    assert_eq!(app.completion.scroll, 50);
    assert!(!mouse::is_track_paging(&app));
}

#[test]
fn held_track_click_pages_down_to_the_end() {
    let mut app = transcript(0);
    app.view.scroll = 90;
    app.view.scroll_target = 90;

    press(&mut app, 9);
    tick(&mut app, REPEAT_DELAY + 20);

    assert_eq!(app.view.scroll_target, 0);
    assert!(!mouse::is_track_paging(&app));
}

#[test]
fn held_track_click_follows_transcript_growth() {
    let mut app = transcript(90);

    press(&mut app, 0);
    assert_eq!(app.view.scroll_target, 10);
    app.view.scroll_target += 20;
    mouse::register_scrollbar(&mut app, MouseTarget::Transcript, BAR, 120, 10, 80);
    tick(&mut app, REPEAT_DELAY + 1);

    assert_eq!(app.view.scroll_target, 40);
}

#[test]
fn held_track_click_stops_when_its_scrollbar_disappears() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 10, 90);

    press(&mut app, 0);
    app.view.mouse_regions.clear();
    tick(&mut app, REPEAT_DELAY + 1);

    assert_eq!(app.completion.scroll, 80);
    assert!(!mouse::is_track_paging(&app));
}

#[test]
fn releasing_stops_repeat() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 10, 90);

    click(&mut app, 0);
    tick(&mut app, REPEAT_DELAY + 5);

    assert_eq!(app.completion.scroll, 80);
    assert!(!mouse::is_track_paging(&app));
}

#[test]
fn leaving_the_track_pauses_repeat_and_returning_resumes() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 10, 90);

    press(&mut app, 0);
    mouse::route(&mut app, drag(BAR.x - 1, 0));
    tick(&mut app, REPEAT_DELAY + 5);
    assert_eq!(app.completion.scroll, 80);
    assert!(!mouse::is_track_paging(&app));

    mouse::route(&mut app, drag(BAR.x, 0));
    tick(&mut app, REPEAT_DELAY + 1);
    assert_eq!(app.completion.scroll, 70);
}

#[test]
fn moving_the_pointer_retargets_the_repeat() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 10, 90);

    press(&mut app, 0);
    mouse::route(&mut app, drag(BAR.x, 7));
    tick(&mut app, REPEAT_DELAY + 20);

    assert_eq!(app.completion.scroll, 70);
}

#[test]
fn wheel_cancels_held_track_click() {
    let mut app = App::default();
    register(&mut app, MouseTarget::Completion, 100, 10, 90);

    press(&mut app, 0);
    mouse::route(&mut app, event(MouseEventKind::ScrollUp, BAR.x, 0));

    assert!(!mouse::is_track_paging(&app));
}

#[test]
fn track_side_and_page_follow_the_thumb() {
    let mut state = State::default();
    state.update_large(BAR, 100, 10, 50);

    assert_eq!(state.track_side((10, 5)), None);
    assert_eq!(state.track_side((9, 0)), None);
    assert_eq!(state.track_side((10, 10)), None);
    assert_eq!(state.track_side((10, 4)), Some(Side::Above));
    assert_eq!(state.track_side((10, 6)), Some(Side::Below));
    assert_eq!(state.page(Side::Above), Some(40));
    assert_eq!(state.page(Side::Below), Some(50));
    assert_eq!(state.track_side((10, 5)), None);
}

fn transcript(position: usize) -> App {
    let mut app = App::default();
    register(&mut app, MouseTarget::Transcript, 100, 10, position);
    app
}

fn register(app: &mut App, target: MouseTarget, virtual_size: usize, window: usize, at: usize) {
    mouse::register_region(app, AREA, target);
    mouse::register_scrollbar(app, target, BAR, virtual_size, window, at);
}

fn tick(app: &mut App, count: usize) {
    for _ in 0..count {
        mouse::repeat_track_page(app);
    }
}

fn press(app: &mut App, row: u16) {
    mouse::route(
        app,
        event(MouseEventKind::Down(MouseButton::Left), BAR.x, row),
    );
}

fn click(app: &mut App, row: u16) {
    press(app, row);
    mouse::route(
        app,
        event(MouseEventKind::Up(MouseButton::Left), BAR.x, row),
    );
}

fn drag(column: u16, row: u16) -> MouseEvent {
    event(MouseEventKind::Drag(MouseButton::Left), column, row)
}

fn event(kind: MouseEventKind, column: u16, row: u16) -> MouseEvent {
    MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    }
}
