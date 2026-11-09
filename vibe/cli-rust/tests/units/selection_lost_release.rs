//! A drag whose release report never arrives still finalizes and copies its selection.

use crossterm::event::{MouseButton, MouseEventKind};

use vibe_rs::app::{App, Severity};
use vibe_rs::mouse;

use crate::support::{message, redraw, row, send_mouse, up};

const WORDS: &str = "Slash commands cannot be queued";

struct Harness {
    app: App,
    /// The cell where the notice's first word starts.
    anchor: (u16, u16),
}

impl Harness {
    fn new() -> Self {
        let mut app = App::default();
        vibe_rs::ui::notice::show_sticky(&mut app, Severity::Warning, WORDS);
        let buffer = redraw(&mut app);
        let anchor = (0..buffer.area.height)
            .find_map(|y| {
                let line = row(&buffer, y);
                line.find(WORDS)
                    .map(|at| (line[..at].chars().count() as u16, y))
            })
            .expect("painted notice");
        Self { app, anchor }
    }

    fn redraw(&mut self) -> ratatui::buffer::Buffer {
        redraw(&mut self.app)
    }

    fn mouse(&mut self, kind: MouseEventKind, at: (u16, u16)) {
        send_mouse(&mut self.app, crate::support::mouse(kind, at.0, at.1));
    }

    /// Press on the notice's first cell and drag over its first word, never releasing.
    fn drag_first_word(&mut self) {
        let (x, y) = self.anchor;
        self.mouse(MouseEventKind::Down(MouseButton::Left), (x, y));
        self.mouse(MouseEventKind::Drag(MouseButton::Left), (x + 4, y));
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
    assert_eq!(harness.copied_text(), None);

    harness.mouse(MouseEventKind::Down(MouseButton::Left), (40, 2));

    let notice = harness
        .app
        .overlays
        .notice
        .as_ref()
        .map(|notice| notice.text.as_str());
    assert!(notice.is_some_and(|text| text.starts_with("Copied")));
}

/// A press past the painted frame: it hits no target, so the notice selection stays.
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
    assert_eq!(harness.copied_text(), None);
}

#[test]
fn a_press_before_any_paint_read_the_selection_leaves_its_copy_for_the_paint() {
    let mut harness = Harness::new();
    let (x, y) = harness.anchor;
    harness.mouse(MouseEventKind::Down(MouseButton::Left), (x, y));
    harness.mouse(MouseEventKind::Drag(MouseButton::Left), (x + 4, y));

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
