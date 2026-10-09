//! Python `ChatTextArea.on_blur`: losing focus collapses the composer selection.

use vibe_rs::app::{App, Surface};
use vibe_rs::selection::blur_composer;

#[test]
fn blur_keeps_the_caret_and_drops_the_selection_and_its_drag() {
    let mut app = App::default();
    app.chat_input.input = "draft text".into();
    app.chat_input.cursor = 6;
    app.chat_input.anchor = Some(10);
    app.selection.composer_anchor = Some(10);
    app.selection.drag = Some(Surface::Composer);

    blur_composer(&mut app);

    assert_eq!(app.chat_input.cursor, 6);
    assert_eq!(app.chat_input.anchor, None);
    assert_eq!(app.selection.composer_anchor, None);
    assert!(app.selection.drag.is_none());
}

#[test]
fn blur_leaves_a_bottom_bar_drag_alone() {
    let mut app = App::default();
    app.selection.drag = Some(Surface::BottomBar);

    blur_composer(&mut app);

    assert!(app.selection.drag == Some(Surface::BottomBar));
}
