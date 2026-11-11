use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::Terminal;

use super::{chrome, draw_tabs, fit, halves, pane, GUTTER, SCROLLBAR_GUTTER};

#[test]
fn the_search_row_costs_one_more_row_of_chrome() {
    assert_eq!((chrome(false), chrome(true)), (6, 7));
}

#[test]
fn a_pane_starts_under_the_title_or_under_the_search_row() {
    let area = Rect::new(2, 10, 40, 20);
    assert_eq!(pane(area, false, 0, 5), Rect::new(3, 13, 38, 5));
    assert_eq!(pane(area, true, 4, 3), Rect::new(3, 18, 38, 3));
}

#[test]
fn lines_that_fit_keep_the_width_without_a_scrollbar() {
    let lines = fit(40, 3, |width| vec![width; 3]);
    assert_eq!(lines, [usize::from(40 - GUTTER); 3]);
}

#[test]
fn overflowing_lines_are_laid_out_again_short_of_the_scrollbar() {
    let lines = fit(40, 2, |width| vec![width; 3]);
    assert_eq!(lines, [usize::from(40 - SCROLLBAR_GUTTER); 3]);
}

#[test]
fn an_odd_width_gives_its_extra_column_to_the_right_half() {
    let (left, right) = halves(Rect::new(0, 0, 141, 10));
    assert_eq!(left, Rect::new(0, 0, 70, 10));
    assert_eq!(right, Rect::new(70, 0, 71, 10));
}

#[test]
fn a_title_row_too_narrow_for_every_tab_keeps_the_open_one() {
    let tabs = [(0, "Mine".to_owned()), (1, "Shared".to_owned())];
    let mut terminal = Terminal::new(TestBackend::new(22, 3)).unwrap();
    let mut areas = Vec::new();
    terminal
        .draw(|f| areas = draw_tabs(f, f.area(), "Skills", &tabs, 1))
        .unwrap();
    assert_eq!(areas, [(1, Rect::new(10, 1, 8, 1))]);
}
