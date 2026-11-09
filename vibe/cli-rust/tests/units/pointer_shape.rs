//! Regression: hovering an expandable entry or the composer left the pointer
//! as an arrow instead of a hand or an I-beam.

use ratatui::layout::Rect;
use serde_json::json;

use vibe_rs::app::App;
use vibe_rs::mouse::{self, MouseTarget};
use vibe_rs::pointer::{shape, Shape};

/// Screen rows the reasoning entry occupies, and a column inside the transcript.
const ENTRY_TOP: u16 = 2;
const ENTRY_BOTTOM: u16 = 5;
const COLUMN: u16 = 5;

/// A settled frame: one expandable reasoning entry over the composer box.
fn hovering(at: (u16, u16)) -> Shape {
    let mut app = App::default();
    app.view.transcript.add(&json!({
        "entry": {"id": "r1", "type": "reasoning", "text": "why", "generationStatus": "completed"}
    }));
    app.view.selection_region.area = Rect::new(0, 0, 40, 10);
    mouse::register_region(&mut app, Rect::new(0, 0, 40, 10), MouseTarget::Transcript);
    app.view.entry_hitmap = vec![(ENTRY_TOP, ENTRY_BOTTOM, Some(ENTRY_TOP), "r1".to_owned())];
    app.view.input_area = Rect::new(0, 10, 40, 3);
    mouse::register_region(&mut app, Rect::new(0, 10, 40, 3), MouseTarget::Composer);
    app.view.mouse_position = Some(at);
    shape(&app)
}

#[test]
fn hovering_an_expandable_entry_asks_for_the_hand() {
    assert_eq!(hovering((COLUMN, ENTRY_TOP + 1)), Shape::Pointer);
}

#[test]
fn hovering_plain_transcript_keeps_the_arrow() {
    assert_eq!(hovering((COLUMN, ENTRY_BOTTOM + 2)), Shape::Default);
}

#[test]
fn hovering_the_composer_asks_for_the_beam() {
    assert_eq!(hovering((COLUMN, 11)), Shape::Text);
}

#[test]
fn hovering_the_jump_to_bottom_chip_asks_for_the_hand() {
    let mut app = App::default();
    mouse::register_region(&mut app, Rect::new(0, 0, 40, 10), MouseTarget::Transcript);
    mouse::register_region(&mut app, Rect::new(10, 9, 18, 1), MouseTarget::JumpToBottom);
    app.view.mouse_position = Some((12, 9));
    assert_eq!(shape(&app), Shape::Pointer);
}

#[test]
fn hovering_the_pinned_prompt_asks_for_the_hand() {
    let mut app = App::default();
    mouse::register_region(&mut app, Rect::new(0, 0, 40, 10), MouseTarget::Transcript);
    mouse::register_region(&mut app, Rect::new(0, 0, 39, 2), MouseTarget::StickyPrompt);
    app.view.mouse_position = Some((5, 1));
    assert_eq!(shape(&app), Shape::Pointer);
}
