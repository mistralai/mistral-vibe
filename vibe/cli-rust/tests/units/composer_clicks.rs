//! Composer clicks through the mouse router: chain wrap, drag reset, right clicks.

use crossterm::event::{MouseButton, MouseEventKind};
use ratatui::layout::Rect;

use vibe_rs::app::App;
use vibe_rs::chat_input::selection_range;
use vibe_rs::mouse::{self, MouseTarget};

use crate::support::{mouse, send_mouse};

const TEXT: &str = "hello world foo";
const ROW: u16 = 11;

struct Composer {
    app: App,
}

impl Composer {
    fn new() -> Self {
        let mut app = App::default();
        let area = Rect::new(0, 10, 40, 3);
        app.view.input_area = area;
        mouse::register_region(&mut app, area, MouseTarget::Composer);
        app.chat_input.input = TEXT.to_owned();
        app.chat_input.cursor = TEXT.len();
        Self { app }
    }

    fn send(&mut self, kind: MouseEventKind, offset: u16) {
        send_mouse(&mut self.app, mouse(kind, offset + 2, ROW));
    }

    fn click(&mut self, offset: u16) {
        self.send(MouseEventKind::Down(MouseButton::Left), offset);
        self.send(MouseEventKind::Up(MouseButton::Left), offset);
    }

    fn selected(&self) -> Option<&'static str> {
        let input = &self.app.chat_input;
        selection_range(&input.input, input.cursor, input.anchor).map(|(lo, hi)| &TEXT[lo..hi])
    }
}

#[test]
fn a_fourth_click_only_places_the_caret_and_the_next_ones_select_again() {
    let mut composer = Composer::new();
    let mut seen = Vec::new();
    for _ in 0..6 {
        composer.click(2);
        seen.push(composer.selected());
    }

    assert_eq!(
        seen,
        [
            None,
            Some("hello"),
            Some(TEXT),
            None,
            Some("hello"),
            Some(TEXT)
        ]
    );
    assert_eq!(composer.app.chat_input.cursor, TEXT.len());
}

#[test]
fn a_click_after_a_word_drag_only_places_the_caret() {
    let mut composer = Composer::new();
    composer.click(2);
    composer.send(MouseEventKind::Down(MouseButton::Left), 2);
    composer.send(MouseEventKind::Drag(MouseButton::Left), 8);
    composer.send(MouseEventKind::Up(MouseButton::Left), 8);
    assert_eq!(composer.selected(), Some("hello world"));

    composer.click(2);

    assert_eq!(composer.selected(), None);
    assert_eq!(composer.app.chat_input.cursor, 2);
}

#[test]
fn a_right_click_neither_moves_the_caret_nor_advances_the_chain() {
    let mut composer = Composer::new();

    composer.send(MouseEventKind::Down(MouseButton::Right), 2);
    composer.send(MouseEventKind::Up(MouseButton::Right), 2);
    assert_eq!(composer.app.chat_input.cursor, TEXT.len());

    composer.click(2);

    assert_eq!(composer.selected(), None);
    assert_eq!(composer.app.chat_input.cursor, 2);
}
