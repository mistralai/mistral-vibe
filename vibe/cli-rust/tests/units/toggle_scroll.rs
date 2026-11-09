//! Expanding or collapsing an entry holds it steady, then reveals what it uncovered.

use vibe_rs::utils::scroll::{anchored_row, bulk_toggle_anchor, offset_at, reveal, ScrollAnchor};

const VIEWPORT: u16 = 20;

fn anchor(row: i32, height: u16, landing: u16) -> ScrollAnchor {
    ScrollAnchor {
        index: 0,
        row,
        height,
        landing,
        inset: 0,
        key: Some("entry".into()),
        reveal: true,
    }
}

#[test]
fn a_visible_header_keeps_its_row() {
    assert_eq!(anchored_row(&anchor(7, 2, 8), 1, 30), 7);
}

#[test]
fn a_cut_off_unchanged_entry_keeps_its_offset() {
    assert_eq!(anchored_row(&anchor(-5, 40, 0), 1, 40), -5);
}

#[test]
fn a_collapse_from_the_body_lands_the_header_on_the_clicked_row() {
    assert_eq!(anchored_row(&anchor(-30, 60, 12), 1, 2), 11);
}

#[test]
fn a_loop_prompt_title_under_two_gap_rows_stays_put() {
    assert_eq!(anchored_row(&anchor(-2, 9, 0), 2, 4), -2);
    assert_eq!(anchored_row(&anchor(-9, 12, 3), 2, 4), 1);
}

#[test]
fn reveal_scrolls_just_enough_to_show_the_block() {
    assert_eq!(reveal(100, VIEWPORT, 115, 125), 105);
}

#[test]
fn reveal_stops_with_the_block_top_at_the_viewport_top() {
    assert_eq!(reveal(100, VIEWPORT, 115, 180), 115);
}

#[test]
fn reveal_leaves_a_block_already_in_view() {
    assert_eq!(reveal(100, VIEWPORT, 105, 118), 100);
}

#[test]
fn reveal_never_lifts_a_cut_off_block_top_further() {
    assert_eq!(reveal(100, VIEWPORT, 99, 150), 100);
}

#[test]
fn offset_clamps_to_the_document() {
    assert_eq!(offset_at(100, VIEWPORT, 30), 50);
    assert_eq!(offset_at(100, VIEWPORT, -4), 80);
    assert_eq!(offset_at(100, VIEWPORT, 95), 0);
}

#[test]
fn ctrl_o_while_pinned_keeps_following_the_newest_content() {
    assert_eq!(bulk_toggle_anchor(0, &[(3, -2, 5)], 0), None);
}

#[test]
fn ctrl_o_while_scrolled_up_anchors_the_top_entry() {
    let anchor = bulk_toggle_anchor(12, &[(3, -2, 5), (4, 3, 2)], 0).expect("anchor");
    assert_eq!((anchor.index, anchor.row, anchor.height), (3, -2, 5));
    assert_eq!(
        (anchor.landing, anchor.key, anchor.reveal),
        (0, None, false)
    );
}

#[test]
fn ctrl_o_under_a_pinned_prompt_anchors_the_first_entry_below_it() {
    let anchor = bulk_toggle_anchor(12, &[(3, -2, 5), (4, 3, 4)], 4).expect("anchor");
    assert_eq!((anchor.index, anchor.row, anchor.landing), (4, 3, 4));
}

#[test]
fn ctrl_o_lands_a_hidden_header_just_under_the_pinned_prompt() {
    let anchor = bulk_toggle_anchor(12, &[(3, -6, 9), (4, 3, 4)], 2).expect("anchor");
    assert_eq!(anchored_row(&anchor, 1, 20), 1);
}

#[test]
fn ctrl_o_folding_an_entry_whose_header_sits_under_the_pinned_prompt_lands_it_below() {
    let anchor = bulk_toggle_anchor(12, &[(3, -1, 6)], 4).expect("anchor");
    assert_eq!(anchored_row(&anchor, 1, 2), 3);
}

#[test]
fn a_click_collapse_whose_header_sits_under_the_pinned_prompt_lands_it_on_the_clicked_row() {
    let anchor = ScrollAnchor {
        inset: 3,
        ..anchor(1, 30, 5)
    };
    assert_eq!(anchored_row(&anchor, 1, 2), 4);
}
