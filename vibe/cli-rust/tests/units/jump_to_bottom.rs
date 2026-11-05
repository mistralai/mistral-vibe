//! The jump-to-bottom chip: its own style over any cell, and hidden when no click can reach it.

use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::Terminal;

use vibe_rs::app::App;
use vibe_rs::ui::jump_to_bottom;

/// Paint the chip over dim/bold cells of a scrolled-up 40x4 transcript; return its first column.
fn paint(app: &mut App) -> (Option<u16>, Terminal<TestBackend>) {
    app.view.scroll_target = 5;
    let area = Rect::new(0, 0, 40, 4);
    let mut terminal = Terminal::new(TestBackend::new(40, 4)).expect("terminal");
    terminal
        .draw(|f| {
            let dimmed = Style::default().add_modifier(Modifier::DIM | Modifier::BOLD);
            f.buffer_mut().set_style(area, dimmed);
            jump_to_bottom::draw(app, f, area);
        })
        .expect("draw");
    let start = (0..40).find(|&x| terminal.backend().buffer()[(x, 3)].symbol() == "J");
    (start, terminal)
}

#[test]
fn the_chip_ignores_the_modifiers_of_the_cells_under_it() {
    let (start, terminal) = paint(&mut App::default());
    let start = start.expect("chip painted");
    for x in start..start + 4 {
        assert!(terminal.backend().buffer()[(x, 3)].modifier.is_empty());
    }
}

#[test]
fn the_chip_hides_without_mouse_capture() {
    let mut app = App::default();
    app.session.startup_config.enable_mouse_capture = false;
    assert_eq!(paint(&mut app).0, None);
    assert!(app.view.mouse_regions.is_empty());
}
