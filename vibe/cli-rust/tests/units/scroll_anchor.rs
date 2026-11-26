//! Regression: while scrolled up, streaming content dragged the viewport down with it.

use vibe_rs::utils::scroll::{absorb_growth, scroll_view};

const VIEWPORT: u16 = 20;

/// Top document line rendered after a frame whose document grew to `total`.
fn top_line(scroll: &mut u16, target: &mut u16, total: u16, last_total: u16) -> u16 {
    absorb_growth(scroll, target, total, last_total);
    scroll_view(total, VIEWPORT, *scroll).position
}

#[test]
fn streaming_does_not_move_a_scrolled_up_viewport() {
    let (mut scroll, mut target) = (30u16, 30u16);
    let before = top_line(&mut scroll, &mut target, 100, 100);
    let after = top_line(&mut scroll, &mut target, 140, 100);
    assert_eq!(before, after);
}
