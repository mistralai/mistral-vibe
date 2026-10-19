//! Click chains: char, word, paragraph, then wrap; a drag or a pause starts a new chain.

use std::time::{Duration, Instant};

use vibe_rs::selection::{ClickChain, Granularity};

const CELL: (u16, u16) = (4, 2);

fn name(granularity: Granularity) -> &'static str {
    match granularity {
        Granularity::Char => "char",
        Granularity::Word => "word",
        Granularity::Paragraph => "paragraph",
    }
}

fn press(chain: &mut ClickChain, at: Instant) -> &'static str {
    name(chain.press(CELL, at))
}

#[test]
fn a_fourth_click_wraps_to_char_and_later_clicks_advance_again() {
    let mut chain = ClickChain::default();
    let now = Instant::now();

    let cycle: Vec<_> = (0..6).map(|_| press(&mut chain, now)).collect();

    assert_eq!(
        cycle,
        ["char", "word", "paragraph", "char", "word", "paragraph"]
    );
}

#[test]
fn a_finished_drag_starts_a_new_chain_but_a_plain_release_keeps_it() {
    let mut chain = ClickChain::default();
    let now = Instant::now();
    press(&mut chain, now);
    chain.release(false);
    assert_eq!(press(&mut chain, now), "word");

    chain.release(true);

    assert_eq!(press(&mut chain, now), "char");
}

#[test]
fn a_click_after_the_chain_window_starts_a_new_chain() {
    let mut chain = ClickChain::default();
    let start = Instant::now();
    press(&mut chain, start);
    assert_eq!(
        press(&mut chain, start + Duration::from_millis(500)),
        "word"
    );

    let late = start + Duration::from_millis(1_001);

    assert_eq!(press(&mut chain, late), "char");
    assert_eq!(press(&mut chain, late), "word");
}

#[test]
fn a_click_on_another_cell_starts_a_new_chain() {
    let mut chain = ClickChain::default();
    let now = Instant::now();
    press(&mut chain, now);

    assert_eq!(name(chain.press((CELL.0 + 1, CELL.1), now)), "char");
}
