//! A link scrolled under the pinned prompt never takes the hover from a pointer over the prompt.

use ratatui::backend::TestBackend;
use ratatui::Terminal;
use vibe_rs::app::App;
use vibe_rs::ui::theme;

use crate::support::{message, session_state};

const WIDTH: u16 = 60;
const HEIGHT: u16 = 40;
/// Scrolled so the link's first row sits under the pinned separator and its second just below.
const SCROLL: u16 = 11;

/// Whether each screen row shows the link hover style, with the pointer at `at`.
fn hovered_rows(at: (u16, u16)) -> Vec<bool> {
    let preface = (1..=20)
        .map(|index| format!("Paragraph {index:02}."))
        .collect::<Vec<_>>()
        .join("\n\n");
    let label =
        "the official Tokio documentation for the select macro, which waits on multiple branches";
    let reply = format!("{preface}\n\nRead [{label}](https://docs.rs/tokio) now.\n\n{preface}");
    let mut app = App::default();
    app.view.transcript.load_snapshot(&session_state(
        "s",
        vec![
            message("u1", "user", "link the docs"),
            message("a1", "assistant", &reply),
        ],
    ));
    app.view.mouse_position = Some(at);
    let mut terminal = Terminal::new(TestBackend::new(WIDTH, HEIGHT)).expect("terminal");
    // The first frames settle the layout cache and the scroll it clamps.
    for _ in 0..3 {
        app.view.scroll = SCROLL;
        app.view.scroll_target = SCROLL;
        terminal.draw(|frame| app.draw(frame)).expect("draw");
    }
    let buffer = terminal.backend().buffer();
    let hover = theme::link_hover_style().bg;
    (0..HEIGHT)
        .map(|y| (0..WIDTH).any(|x| Some(buffer[(x, y)].bg) == hover))
        .collect()
}

#[test]
fn hovering_the_pinned_prompt_over_a_hidden_link_row_highlights_nothing() {
    assert!(!hovered_rows((10, 1)).contains(&true));
}

#[test]
fn hovering_the_visible_link_row_still_highlights_it() {
    assert!(hovered_rows((10, 2))[2]);
}
