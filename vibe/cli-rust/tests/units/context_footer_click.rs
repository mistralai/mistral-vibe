//! A plain click on the footer's context gauge opens `/context`; drags and clicks
//! elsewhere do not, and it works while a turn generates (side-channel).

use tokio::sync::mpsc;

use vibe_rs::app::{App, Status};
use vibe_rs::telemetry::{self, event, TelemetryEvent};
use vibe_rs::ui::bottom_bar;

use crate::support::{bottom_bar_row, click, column, mouse, send_mouse};
use crossterm::event::{MouseButton, MouseEventKind};

const WIDTH: u16 = 120;

/// The footer of a ready session painted at row 0, and the column the context gauge starts at.
fn footer() -> (App, u16) {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (12_500, 600_000);
    let row = bottom_bar_row(&mut app, WIDTH);
    let start = column(&row, "ctx ");
    (app, start)
}

#[tokio::test]
async fn a_plain_click_on_the_counter_opens_the_context_app_and_records_the_footer_entry() {
    let (mut app, start) = footer();
    app.session.session_id = Some("session-1".into());
    let (tx, mut rx) = mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);

    click(&mut app, start + 3, 0);

    assert!(app.context_app.open);
    let events: Vec<TelemetryEvent> = std::iter::from_fn(|| rx.try_recv().ok()).collect();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].name, event::SLASH_COMMAND_USED);
    assert_eq!(events[0].properties["command"], "context");
    assert_eq!(events[0].properties["entry_point"], "footer");
}

#[tokio::test]
async fn the_counter_stays_clickable_while_a_turn_generates() {
    let (mut app, start) = footer();
    app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
    click(&mut app, start + 3, 0);
    assert!(app.context_app.open);
}

#[tokio::test]
async fn a_drag_over_the_counter_does_not_open_the_app() {
    let (mut app, start) = footer();
    send_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), start, 0),
    );
    send_mouse(
        &mut app,
        mouse(MouseEventKind::Drag(MouseButton::Left), start + 5, 0),
    );
    send_mouse(
        &mut app,
        mouse(MouseEventKind::Up(MouseButton::Left), start + 5, 0),
    );
    assert!(!app.context_app.open);
}

#[tokio::test]
async fn a_click_on_the_cwd_does_not_open_the_context_app() {
    let (mut app, counter) = footer();
    assert!(bottom_bar::tokens_at(&app, (counter + 1, 0)));
    assert!(!bottom_bar::tokens_at(&app, (counter - 1, 0)));
    click(&mut app, 1, 0);
    assert!(!app.context_app.open);
}
