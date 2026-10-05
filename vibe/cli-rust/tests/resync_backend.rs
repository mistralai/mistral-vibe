//! The backend moves the cursor explicitly after multi-codepoint graphemes and emoji-prone
//! single codepoints, so terminal width drift stops there.

use ratatui::backend::{Backend, CrosstermBackend};
use ratatui::buffer::Cell;
use ratatui::style::{Color, Modifier, Style};
use vibe_rs::resync_backend::ResyncBackend;

fn drawn(symbols: &[&'static str]) -> String {
    let cells: Vec<Cell> = symbols.iter().map(|symbol| Cell::new(symbol)).collect();
    drawn_cells(&cells)
}

fn drawn_cells(cells: &[Cell]) -> String {
    let mut out = Vec::new();
    let row = cells
        .iter()
        .enumerate()
        .map(|(x, cell)| (x as u16, 0, cell));
    ResyncBackend::new(&mut out).draw(row).unwrap();
    String::from_utf8(out).unwrap()
}

#[test]
fn cell_after_a_cluster_is_drawn_at_its_own_column() {
    let out = drawn(&["a", "ណ្ដ", " ", "▇"]);
    assert!(out.contains("\x1b[1;3H ▇"), "{out:?}");
    assert_eq!(out.matches("\x1b[1;").count(), 2, "{out:?}");
}

#[test]
fn single_codepoint_cells_get_separate_moves() {
    let out = drawn(&["a", "▶", "⚠", "▇"]);
    // Moves before "a", after "▶" and after "⚠".
    assert_eq!(out.matches("\x1b[1;").count(), 3, "{out:?}");
}

#[test]
fn letters_and_borders_skip_the_move() {
    let out = drawn(&[
        "─", "▇", "│", "é", "•", "ж", "λ", "ᠷ", "⠋", "·", "⎢", "⎣", "b",
    ]);
    assert_eq!(out.matches("\x1b[1;").count(), 1, "{out:?}");
}

#[test]
fn non_ascii_single_codepoint_gets_its_own_move() {
    // Flag emoji U+1F1FA: unicode-width says 1, terminals draw 2. Resync stops drift.
    let out = drawn(&["a", "🇺", "b", "▇"]);
    // Moves before "a" and after "🇺".
    assert_eq!(out.matches("\x1b[1;").count(), 2, "{out:?}");
}

#[test]
fn resync_does_not_reemit_styles() {
    let mut cell = Cell::new("⚠");
    cell.set_style(Style::new().fg(Color::Red).add_modifier(Modifier::BOLD));
    let out = drawn_cells(&[cell.clone(), cell.clone(), cell]);
    assert_eq!(out.matches("\x1b[1;").count(), 3, "{out:?}");
    assert_eq!(out.matches("\x1b[38;5;").count(), 1, "{out:?}");
    assert_eq!(out.matches("\x1b[1m").count(), 1, "{out:?}");
    assert_eq!(out.matches("\x1b[39m").count(), 1, "{out:?}");
}

#[test]
fn ascii_output_matches_crossterm_backend() {
    let styles = [
        Style::new().add_modifier(Modifier::BOLD | Modifier::DIM),
        Style::new().fg(Color::Red).add_modifier(Modifier::BOLD),
        Style::new()
            .bg(Color::Blue)
            .add_modifier(Modifier::REVERSED | Modifier::ITALIC),
        Style::new()
            .underline_color(Color::Green)
            .add_modifier(Modifier::UNDERLINED),
        Style::new().add_modifier(Modifier::SLOW_BLINK | Modifier::CROSSED_OUT),
        Style::new(),
    ];
    let cells: Vec<Cell> = styles
        .iter()
        .map(|style| {
            let mut cell = Cell::new("x");
            cell.set_style(*style);
            cell
        })
        .collect();
    let mut expected = Vec::new();
    let row = cells
        .iter()
        .enumerate()
        .map(|(x, cell)| (x as u16, 0, cell));
    CrosstermBackend::new(&mut expected).draw(row).unwrap();
    assert_eq!(drawn_cells(&cells), String::from_utf8(expected).unwrap());
}

#[test]
fn cluster_starting_with_ascii_gets_its_own_move() {
    let out = drawn(&["e\u{301}", "b"]);
    assert_eq!(out.matches("\x1b[1;").count(), 2, "{out:?}");
}
