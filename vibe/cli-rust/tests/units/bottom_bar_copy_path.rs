//! The copy icon after the footer's cwd copies the whole path, even when the cwd is shortened.

use crossterm::event::{MouseButton, MouseEventKind};
use tokio::sync::mpsc;

use vibe_rs::app::{App, Status};
use vibe_rs::pointer::{self, Shape};
use vibe_rs::telemetry::{self, event, TelemetryEvent};
use vibe_rs::ui::bottom_bar::{self, BarLinkAction};

use crate::support::{bottom_bar, bottom_bar_row, click, column, mouse, send_mouse};

const CWD: &str = "~/work/dashboard/vibe/cli-rust";
const WIDTH: u16 = 30;

/// A narrow footer that shortens the cwd, and the column of its copy icon.
fn footer() -> (App, u16) {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.session.cwd = Some(CWD.into());
    app.session.tokens = (0, 600_000);
    let row = bottom_bar_row(&mut app, WIDTH);
    assert!(row.starts_with("…/vibe/cli-rust ⧉ "), "{row:?}");
    let icon = column(&row, "⧉");
    (app, icon)
}

/// The text lengths of the `user_copied_text` events recorded so far.
fn copied_lengths(rx: &mut mpsc::Receiver<TelemetryEvent>) -> Vec<u64> {
    std::iter::from_fn(|| rx.try_recv().ok())
        .filter(|event| event.name == event::USER_COPIED_TEXT)
        .filter_map(|event| event.properties["text_length"].as_u64())
        .collect()
}

#[test]
fn a_click_on_the_icon_copies_the_whole_cwd() {
    let (mut app, icon) = footer();
    let (tx, mut rx) = mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);

    click(&mut app, icon, 0);

    let notice = app.overlays.notice.as_ref().expect("copy notice");
    assert!(notice.text.starts_with("Copied"), "{:?}", notice.text);
    assert_eq!(copied_lengths(&mut rx), [CWD.chars().count() as u64]);
    assert!(app.selection.bottom_bar.is_none(), "no selection is armed");
}

#[test]
fn a_double_click_on_the_icon_copies_the_path_twice_and_selects_nothing() {
    let (mut app, icon) = footer();
    let (tx, mut rx) = mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);
    let length = CWD.chars().count() as u64;

    click(&mut app, icon, 0);
    bottom_bar_row(&mut app, WIDTH);
    click(&mut app, icon, 0);

    assert_eq!(copied_lengths(&mut rx), [length, length]);
    assert!(
        app.selection.bottom_bar.is_none(),
        "no word is armed to copy"
    );
}

#[test]
fn the_icon_is_the_only_hit_area_and_carries_the_whole_path() {
    let (app, icon) = footer();

    let link = bottom_bar::link_at(&app, (icon, 0)).expect("icon link");
    assert!(matches!(&link.action, BarLinkAction::CopyPath(path) if path == CWD));
    assert_eq!(link.area.width, 1);
    assert!(
        bottom_bar::link_at(&app, (icon - 2, 0)).is_none(),
        "the cwd"
    );
    assert!(bottom_bar::link_at(&app, (icon + 1, 0)).is_none());
}

#[test]
fn hovering_the_icon_shows_the_hand_but_keeps_its_look() {
    let (mut app, icon) = footer();
    let plain = bottom_bar(&mut app, WIDTH)[(icon, 0)].style();

    send_mouse(&mut app, mouse(MouseEventKind::Moved, icon, 0));
    let buffer = bottom_bar(&mut app, WIDTH);

    assert_eq!(pointer::shape(&app), Shape::Pointer);
    assert_eq!(buffer[(icon, 0)].style(), plain);
}

#[test]
fn a_drag_over_the_icon_selects_instead_of_copying_the_path() {
    let (mut app, icon) = footer();
    let (tx, mut rx) = mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);

    send_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 2, 0),
    );
    send_mouse(
        &mut app,
        mouse(MouseEventKind::Drag(MouseButton::Left), icon, 0),
    );
    send_mouse(
        &mut app,
        mouse(MouseEventKind::Up(MouseButton::Left), icon, 0),
    );

    assert!(copied_lengths(&mut rx).is_empty());
    assert!(
        app.selection.bottom_bar.is_some(),
        "the drag keeps its selection"
    );
}

#[test]
fn the_icon_is_inert_under_an_overlay_drawn_over_the_footer() {
    let (mut app, icon) = footer();
    app.config_screen.open = true;

    assert!(bottom_bar::link_at(&app, (icon, 0)).is_none());
}

#[test]
fn without_a_cwd_there_is_no_icon() {
    let mut app = App::default();
    app.session.tokens = (0, 600_000);

    let row = bottom_bar_row(&mut app, WIDTH);

    assert!(row.starts_with("- "), "{row:?}");
    assert!(!row.contains('⧉'));
    assert!(app.view.bottom_bar_links.is_empty());
}
