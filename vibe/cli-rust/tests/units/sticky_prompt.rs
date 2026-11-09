//! The pinned prompt follows the section at the viewport top and is pushed out by the next one.

use serde_json::Value;
use vibe_rs::transcript::Transcript;
use vibe_rs::ui::transcript::sticky::{pin, Pin};
use vibe_rs::utils::transcript_cache::{LayoutEntry, TranscriptCache};

use crate::support::{message, session_state};

const TOP: u16 = 5;
const HEIGHT: u16 = 20;

/// The pin at document row `position` over `history`, laid out as `(top, height)` per entry.
fn pin_over(history: Vec<Value>, rows: &[(u16, u16)], position: u16) -> Option<Pin> {
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
    let prompts = (0..rows.len())
        .filter(|&index| transcript.settled_prompt(index).is_some())
        .collect();
    let mut cache = TranscriptCache::default();
    cache.ensure_context(80, 79, 0, false);
    cache.store_layout(transcript.revision(), 80, 80, entries, None, prompts);
    let layout = cache.layout(transcript.revision(), 80)?;
    pin(layout, &transcript, TOP, position, HEIGHT)
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
            next: Some(10)
        })
    );
    assert_eq!(
        at(TOP + 39),
        Some(Pin {
            index: 0,
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
            next: Some(0)
        })
    );
    assert_eq!(
        pin_over(history, &rows, TOP + 6),
        Some(Pin {
            index: 1,
            next: None
        })
    );
}
