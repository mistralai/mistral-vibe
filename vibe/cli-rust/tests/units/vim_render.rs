//! Vim mode on the composer: Insert mode is labelled with a terminal bar caret, Normal mode is not.

use ratatui::backend::TestBackend;
use ratatui::layout::Position;
use ratatui::Terminal;
use vibe_rs::vim::Mode;

use crate::support::rows;
use crate::vim::app;

/// The screen, and the terminal cursor while it is shown.
fn draw(mode: Mode) -> (String, Option<Position>) {
    let mut app = app(mode, "hi", 2);
    let mut terminal = Terminal::new(TestBackend::new(80, 24)).expect("terminal");
    terminal.draw(|frame| app.draw(frame)).expect("draw");
    let backend = terminal.backend();
    let cursor = backend.cursor_visible().then(|| backend.cursor_position());
    (rows(backend.buffer()).join("\n"), cursor)
}

#[test]
fn insert_mode_is_labelled_and_shows_the_terminal_cursor_after_the_text() {
    let (screen, cursor) = draw(Mode::Insert);
    let (y, line) = screen
        .lines()
        .enumerate()
        .find(|(_, line)| line.starts_with("> hi"))
        .expect("composer row");
    assert_eq!(cursor, Some(Position::new(4, y as u16)), "{line}");
    let border = screen.lines().skip(y).find(|line| line.starts_with('─'));
    assert!(
        border.is_some_and(|line| line.starts_with("─ INSERT ─")),
        "{screen}"
    );
}

#[test]
fn normal_mode_has_no_label_and_keeps_the_painted_caret() {
    let (screen, cursor) = draw(Mode::Normal);
    assert_eq!(cursor, None);
    assert!(
        !screen.contains("INSERT") && !screen.contains("NORMAL"),
        "{screen}"
    );
}
