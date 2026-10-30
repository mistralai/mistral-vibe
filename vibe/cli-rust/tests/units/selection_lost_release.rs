//! A drag whose release report never arrives still finalizes and copies its selection.

use crossterm::event::{MouseButton, MouseEventKind};

use vibe_rs::app::{App, ToastSeverity};
use vibe_rs::mouse;

use crate::support::{message, redraw, row, send_mouse, up};

const TOAST: &str = "Slash commands cannot be queued";

struct Harness {
    app: App,
}

impl Harness {
    fn new() -> Self {
        let mut app = App::default();
        app.show_toast(TOAST.to_owned(), ToastSeverity::Warning, 60);
        let mut harness = Self { app };
        harness.redraw();
        harness
    }

    fn redraw(&mut self) -> ratatui::buffer::Buffer {
        redraw(&mut self.app)
    }

    fn mouse(&mut self, kind: MouseEventKind, at: (u16, u16)) {
        send_mouse(&mut self.app, crate::support::mouse(kind, at.0, at.1));
    }

    /// Press on the toast's first cell and drag over its first word, never releasing.
    fn drag_first_word(&mut self) {
        let (_, area) = *self.app.view.toast_text_areas.last().expect("toast row");
        self.mouse(MouseEventKind::Down(MouseButton::Left), (area.x, area.y));
        self.mouse(
            MouseEventKind::Drag(MouseButton::Left),
            (area.x + 4, area.y),
        );
        self.redraw();
    }

    fn copied_text(&self) -> Option<&str> {
        let notice = self.app.overlays.notice.as_ref()?;
        notice.text.starts_with("Copied").then_some(())?;
        self.app
            .selection
            .region
            .as_ref()
            .map(|sel| sel.text.as_str())
    }
}

#[test]
fn a_drag_without_a_release_has_not_copied_yet() {
    let mut harness = Harness::new();

    harness.drag_first_word();

    assert_eq!(harness.copied_text(), None);
}

#[test]
fn motion_without_a_button_ends_the_drag_and_copies_its_selection() {
    let mut harness = Harness::new();
    harness.drag_first_word();

    harness.mouse(MouseEventKind::Moved, (40, 2));
    harness.redraw();

    assert_eq!(harness.copied_text(), Some("Slash"));
}

#[test]
fn a_press_after_a_lost_release_copies_the_previous_selection() {
    let mut harness = Harness::new();
    harness.drag_first_word();
    assert!(harness.app.overlays.notice.is_none());

    harness.mouse(MouseEventKind::Down(MouseButton::Left), (40, 2));

    let notice = harness
        .app
        .overlays
        .notice
        .as_ref()
        .map(|notice| notice.text.as_str());
    assert!(notice.is_some_and(|text| text.starts_with("Copied")));
}

/// A press past the painted frame: it hits no target, so the toast selection stays.
const NOWHERE: (u16, u16) = (200, 100);

fn selected(harness: &Harness) -> Option<(&str, bool)> {
    let selection = harness.app.selection.region.as_ref()?;
    Some((selection.text.as_str(), selection.pending_copy))
}

#[test]
fn a_press_on_no_target_after_a_lost_release_keeps_the_text_for_the_copy_key() {
    let mut harness = Harness::new();
    harness.drag_first_word();

    harness.mouse(MouseEventKind::Down(MouseButton::Left), NOWHERE);

    assert_eq!(selected(&harness), Some(("Slash", false)));
    assert_eq!(harness.copied_text(), Some("Slash"));
}

#[test]
fn a_press_on_no_target_after_a_lost_release_caches_a_transcript_selection_for_the_copy_key() {
    const WORDS: &str = "copy these words";
    let mut harness = Harness::new();
    harness
        .app
        .view
        .transcript
        .add(&serde_json::json!({"entry": message("u1", "user", WORDS)}));
    let buffer = harness.redraw();
    let (column, row) = (0..24)
        .find_map(|y| {
            let line = row(&buffer, y);
            line.find(WORDS)
                .map(|at| (line[..at].chars().count() as u16, y))
        })
        .expect("painted words");
    harness.mouse(MouseEventKind::Down(MouseButton::Left), (column, row));
    harness.mouse(MouseEventKind::Drag(MouseButton::Left), (column + 15, row));
    harness.redraw();

    harness.mouse(MouseEventKind::Down(MouseButton::Left), NOWHERE);

    assert_eq!(selected(&harness), Some((WORDS, false)));
}

#[test]
fn with_autocopy_off_a_press_after_a_lost_release_keeps_the_text_without_copying() {
    let mut harness = Harness::new();
    harness.app.session.startup_config.autocopy_to_clipboard = false;
    harness.drag_first_word();

    harness.mouse(MouseEventKind::Down(MouseButton::Left), NOWHERE);

    assert_eq!(selected(&harness), Some(("Slash", false)));
    assert!(harness.app.overlays.notice.is_none());
}

#[test]
fn a_press_before_any_paint_read_the_selection_leaves_its_copy_for_the_paint() {
    let mut harness = Harness::new();
    let (_, area) = *harness.app.view.toast_text_areas.last().expect("toast row");
    harness.mouse(MouseEventKind::Down(MouseButton::Left), (area.x, area.y));
    harness.mouse(
        MouseEventKind::Drag(MouseButton::Left),
        (area.x + 4, area.y),
    );

    harness.mouse(MouseEventKind::Down(MouseButton::Left), NOWHERE);
    assert_eq!(selected(&harness), Some(("", true)));
    harness.redraw();

    assert_eq!(harness.copied_text(), Some("Slash"));
}

#[test]
fn a_release_after_the_lost_one_is_ignored() {
    let mut harness = Harness::new();
    harness.drag_first_word();
    harness.mouse(MouseEventKind::Moved, (40, 2));

    assert_eq!(mouse::route(&mut harness.app, up(40, 2)), None);
}
