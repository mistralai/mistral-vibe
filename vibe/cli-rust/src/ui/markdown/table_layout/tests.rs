use ratatui::style::Style;

use super::{Fit, MIN_WIDTH};
use crate::ui::markdown::Sc;

fn cell(text: &str) -> Vec<Sc> {
    text.chars().map(|c| (c, Style::default(), None)).collect()
}

#[test]
fn widths_stay_valid_once_the_layout_budget_runs_out() {
    let headers = vec![cell("Token"), cell("Prose"), cell("Tiny")];
    let rows: Vec<Vec<Vec<Sc>>> = (0..2_000)
        .map(|row| {
            vec![
                cell(&"t".repeat(1_000 + row % 50)),
                cell(&format!("some prose words for row {row} that wrap")),
                cell("x"),
            ]
        })
        .collect();
    let natural = vec![1_051, 41, 6];
    let total_space = 584;
    let start = vec![551, 28, 5];
    let mut fit = Fit::new(&headers, &rows, natural.clone(), total_space);
    let widths = fit.climb(start);
    assert_eq!(fit.budget, 0);
    assert_eq!(widths.iter().sum::<usize>(), total_space, "{widths:?}");
    for (width, natural) in widths.iter().zip(natural) {
        assert!((MIN_WIDTH..=natural).contains(width), "{widths:?}");
    }
}
