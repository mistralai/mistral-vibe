//! The composer's current-line background stays put while only the caret blinks.

use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::style::Color;
use ratatui::Terminal;
use vibe_rs::app::App;
use vibe_rs::ui::{chat_input, theme};

const WIDTH: u16 = 40;
/// A cell on the caret's row, past the typed text and the caret block.
const TAIL: (u16, u16) = (WIDTH - 3, 1);

fn tail_bg(app: &App) -> Color {
    let mut terminal = Terminal::new(TestBackend::new(WIDTH, 3)).expect("terminal");
    terminal
        .draw(|f| {
            chat_input::draw(app, f, Rect::new(0, 0, WIDTH, 3));
        })
        .expect("draw");
    terminal.backend().buffer()[TAIL].bg
}

#[test]
fn the_cursor_line_background_does_not_blink_with_the_caret() {
    theme::set_active("textual-dark");
    let line_bg = theme::input_cursor_line_bg();
    assert_ne!(line_bg, theme::background());
    let mut app = App::default();
    app.chat_input.input = "draft".into();
    app.chat_input.cursor = app.chat_input.input.len();

    for phase in [true, false, true] {
        app.view.cursor_on = phase;
        assert_eq!(tail_bg(&app), line_bg, "blink phase {phase}");
    }

    app.set_app_focus(false);
    assert_ne!(tail_bg(&app), line_bg);
}
