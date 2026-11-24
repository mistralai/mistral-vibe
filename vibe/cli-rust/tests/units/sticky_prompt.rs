//! The pinned prompt follows the section at the viewport top and is pushed out by the next one.

use serde_json::Value;
use vibe_rs::transcript::Transcript;
use vibe_rs::ui::transcript::sticky::{pin, Pin, MIN_STEER_HEIGHT};
use vibe_rs::utils::transcript_cache::{LayoutEntry, TranscriptCache};

use crate::support::{message, session_state};

const TOP: u16 = 5;
const HEIGHT: u16 = 20;

/// The pin at document row `position` over `history`, laid out as `(top, height)` per entry.
fn pin_over(history: Vec<Value>, rows: &[(u16, u16)], position: u16) -> Option<Pin> {
    pin_in(history, rows, position, HEIGHT)
}

/// The pin over `history` in a viewport `height` rows tall.
fn pin_in(history: Vec<Value>, rows: &[(u16, u16)], position: u16, height: u16) -> Option<Pin> {
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&session_state("s", history));
    let entries = rows
        .iter()
        .enumerate()
        .map(|(index, &(top, height))| LayoutEntry {
            index,
            top,
            height,
            prewrapped: true,
        })
        .collect();
    let (steers, prompts): (Vec<usize>, Vec<usize>) = (0..rows.len())
        .filter(|&index| transcript.settled_prompt(index).is_some())
        .partition(|&index| transcript.entry(index).is_some_and(|entry| entry.steer));
    let mut cache = TranscriptCache::default();
    cache.ensure_context(80, 79, 0, false, false);
    cache
        .store_layout(transcript.revision(), 80, 80, entries, None, prompts)
        .steers = steers;
    let layout = cache.layout(transcript.revision(), 80)?;
    pin(layout, &transcript, TOP, position, height)
}

/// Two sections: a prompt at rows 0-4 then a reply to 40, a prompt at 40-44 then a reply to 80.
fn at(position: u16) -> Option<Pin> {
    let history = vec![
        message("u1", "user", "first"),
        message("a1", "assistant", "reply"),
        message("u2", "user", "second"),
        message("a2", "assistant", "reply"),
    ];
    pin_over(history, &[(0, 5), (5, 35), (40, 5), (45, 35)], position)
}

#[test]
fn nothing_pins_while_the_prompt_text_is_still_in_view() {
    assert_eq!(at(0), None);
    assert_eq!(at(TOP + 2), None);
}

#[test]
fn the_prompt_pins_once_its_text_scrolls_above_the_viewport() {
    assert_eq!(
        at(TOP + 3),
        Some(Pin {
            index: 0,
            steer: None,
            next: None
        })
    );
}

#[test]
fn the_next_prompt_entering_the_viewport_pushes_the_pinned_one() {
    assert_eq!(
        at(TOP + 30),
        Some(Pin {
            index: 0,
            steer: None,
            next: Some(10)
        })
    );
    assert_eq!(
        at(TOP + 39),
        Some(Pin {
            index: 0,
            steer: None,
            next: Some(1)
        })
    );
}

#[test]
fn the_pin_switches_to_the_section_at_the_viewport_top() {
    assert_eq!(
        at(TOP + 43),
        Some(Pin {
            index: 2,
            steer: None,
            next: None
        })
    );
}

#[test]
fn a_transcript_without_a_prompt_pins_nothing() {
    let history = vec![message("a1", "assistant", "reply")];
    assert_eq!(pin_over(history, &[(0, 60)], TOP + 30), None);
}

#[test]
fn a_prompt_packed_against_the_previous_one_pins_with_no_gap_rows() {
    let mut packed = message("u2", "user", "second");
    packed["followsUser"] = true.into();
    let history = vec![
        message("u1", "user", "first"),
        packed,
        message("a1", "assistant", "reply"),
    ];
    let rows = [(0, 5), (5, 2), (7, 60)];
    assert_eq!(
        pin_over(history.clone(), &rows, TOP + 5),
        Some(Pin {
            index: 0,
            steer: None,
            next: Some(0)
        })
    );
    assert_eq!(
        pin_over(history, &rows, TOP + 6),
        Some(Pin {
            index: 1,
            steer: None,
            next: None
        })
    );
}

/// A prompt at rows 0-4, a reply to 30, a steer at 30-32, a reply to 80, then a second prompt at 80.
fn steered_at(position: u16) -> Option<Pin> {
    steered_in(position, MIN_STEER_HEIGHT)
}

fn steered_in(position: u16, height: u16) -> Option<Pin> {
    steered_over(position, height, false)
}

fn steered_over(position: u16, height: u16, packed: bool) -> Option<Pin> {
    let mut steer = message("s1", "user", "also the docs");
    steer["source"] = "turn_steer".into();
    steer["followsUser"] = packed.into();
    let history = vec![
        message("u1", "user", "first"),
        message("a1", "assistant", "reply"),
        steer,
        message("a2", "assistant", "reply"),
        message("u2", "user", "second"),
        message("a3", "assistant", "reply"),
    ];
    let rows = [(0, 5), (5, 25), (30, 3), (33, 47), (80, 5), (85, 40)];
    pin_in(history, &rows, position, height)
}

#[test]
fn a_steer_pins_under_its_prompt_once_its_text_slides_under_the_pin() {
    let pinned = |position| steered_at(position).map(|pin| (pin.index, pin.steer));
    assert_eq!(pinned(TOP + 28), Some((0, None)));
    assert_eq!(pinned(TOP + 29), Some((0, Some(2))));
    assert_eq!(pinned(TOP + 70), Some((0, Some(2))));
}

#[test]
fn the_next_prompt_drops_the_steer_of_the_turn_before() {
    assert_eq!(
        steered_at(TOP + 83).map(|pin| (pin.index, pin.steer)),
        Some((4, None))
    );
}

#[test]
fn a_short_viewport_pins_the_prompt_without_its_steer() {
    let pinned = steered_in(TOP + 70, MIN_STEER_HEIGHT - 1);
    assert_eq!(pinned.map(|pin| (pin.index, pin.steer)), Some((0, None)));
}

#[test]
fn a_packed_steer_pins_as_soon_as_its_text_slides_under_the_pin() {
    let pinned = |position| steered_over(position, MIN_STEER_HEIGHT, true).map(|pin| pin.steer);
    assert_eq!(pinned(TOP + 27), Some(None));
    assert_eq!(pinned(TOP + 28), Some(Some(2)));
}
