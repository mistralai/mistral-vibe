//! Unit tests for the banner cat's click-area math.

use ratatui::layout::Rect;

use super::cat_click_area;

fn rect(x: u16, y: u16, width: u16, height: u16) -> Rect {
    Rect::new(x, y, width, height)
}

#[test]
fn the_fresh_banner_click_area_covers_the_cat_rows() {
    assert_eq!(
        cat_click_area(rect(0, 5, 80, 20), 0),
        Some(rect(0, 6, 14, 3))
    );
}

#[test]
fn a_partially_scrolled_cat_keeps_its_painted_rows() {
    assert_eq!(
        cat_click_area(rect(0, 5, 80, 20), 1),
        Some(rect(0, 5, 14, 3))
    );
    assert_eq!(
        cat_click_area(rect(0, 5, 80, 20), 2),
        Some(rect(0, 5, 14, 2))
    );
    assert_eq!(
        cat_click_area(rect(0, 5, 80, 20), 3),
        Some(rect(0, 5, 14, 1))
    );
}

#[test]
fn a_cat_scrolled_past_the_block_has_no_click_area() {
    assert_eq!(cat_click_area(rect(0, 5, 80, 20), 4), None);
    assert_eq!(cat_click_area(rect(0, 5, 80, 20), 13), None);
}

#[test]
fn the_click_area_clips_to_the_painted_block() {
    // A block only two rows tall paints the gap and the first cat row.
    assert_eq!(
        cat_click_area(rect(2, 5, 80, 2), 0),
        Some(rect(2, 6, 14, 1))
    );
    assert_eq!(cat_click_area(rect(2, 5, 80, 0), 0), None);
}

#[test]
fn the_click_area_clips_to_the_painted_width() {
    assert_eq!(cat_click_area(rect(0, 5, 8, 20), 0), Some(rect(0, 6, 8, 3)));
}
