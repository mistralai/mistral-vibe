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
    ResyncBackend::with_truecolor(&mut out, true)
        .draw(row)
        .unwrap();
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

fn drawn_rgb(truecolor: bool) -> String {
    let mut cell = Cell::new("x");
    cell.set_style(
        Style::new()
            .fg(Color::Rgb(0xFF, 0x82, 0x05))
            .bg(Color::Rgb(0x12, 0x12, 0x12)),
    );
    let mut out = Vec::new();
    ResyncBackend::with_truecolor(&mut out, truecolor)
        .draw(std::iter::once((0, 0, &cell)))
        .unwrap();
    String::from_utf8(out).unwrap()
}

#[test]
fn truecolor_terminal_gets_rgb_sgr() {
    let out = drawn_rgb(true);
    assert!(out.contains("38;2;255;130;5"), "{out:?}");
    assert!(out.contains("48;2;18;18;18"), "{out:?}");
}

#[test]
fn terminal_without_truecolor_gets_the_256_color_palette() {
    // Terminal.app reads `38;2;255;130;5` as separate attributes: the trailing 5 blinks.
    let out = drawn_rgb(false);
    assert!(!out.contains(";2;"), "{out:?}");
    assert!(out.contains("38;5;208"), "{out:?}");
    assert!(out.contains("48;5;233"), "{out:?}");
}

#[test]
fn downgraded_cells_keep_underline_color_and_resync_moves() {
    let rgb = Color::Rgb(0xFF, 0x82, 0x05);
    let cells: Vec<Cell> = ["a", "ណ្ដ", " ", "▇"]
        .iter()
        .map(|symbol| {
            let mut cell = Cell::new(symbol);
            cell.set_style(
                Style::new()
                    .underline_color(rgb)
                    .add_modifier(Modifier::UNDERLINED),
            );
            cell
        })
        .collect();
    let mut out = Vec::new();
    let row = cells
        .iter()
        .enumerate()
        .map(|(x, cell)| (x as u16, 0, cell));
    ResyncBackend::with_truecolor(&mut out, false)
        .draw(row)
        .unwrap();
    let out = String::from_utf8(out).unwrap();
    assert!(out.contains("58;5;208"), "{out:?}");
    assert!(!out.contains(";2;"), "{out:?}");
    assert!(out.contains("\x1b[1;3H ▇"), "{out:?}");
}

#[test]
fn cluster_starting_with_ascii_gets_its_own_move() {
    let out = drawn(&["e\u{301}", "b"]);
    assert_eq!(out.matches("\x1b[1;").count(), 2, "{out:?}");
}
