//! A typed-ahead line break at the end submits; one earlier, or any other last key, stays in the draft.

use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use vibe_rs::typeahead::{take_submit, SUBMIT};

use crate::support::{ctrl, key};

fn typed(text: &str) -> Vec<Event> {
    text.chars()
        .map(|character| match character {
            '\n' => Event::Key(ctrl('j')),
            character => Event::Key(key(KeyCode::Char(character))),
        })
        .collect()
}

#[test]
fn a_trailing_line_feed_becomes_the_submit() {
    let mut events = typed("fix\ntests\n");
    assert_eq!(take_submit(&mut events), Some(SUBMIT));
    assert_eq!(events, typed("fix\ntests"));
}

#[test]
fn text_without_a_trailing_line_break_stays_a_draft() {
    for text in ["fix\ntests", "", "fix\n "] {
        let mut events = typed(text);
        assert_eq!(take_submit(&mut events), None, "{text:?}");
        assert_eq!(events, typed(text));
    }
}

#[test]
fn a_raw_enter_submits() {
    let mut events = typed("go");
    events.push(Event::Key(key(KeyCode::Enter)));
    assert_eq!(take_submit(&mut events), Some(SUBMIT));
    assert_eq!(events, typed("go"));
}

#[test]
fn ctrl_enter_submits_only_from_a_windows_console() {
    let mut events = typed("go");
    events.push(Event::Key(KeyEvent::new(
        KeyCode::Enter,
        KeyModifiers::CONTROL,
    )));
    let expected = cfg!(windows).then_some(SUBMIT);
    assert_eq!(take_submit(&mut events), expected);
}

#[test]
fn a_trailing_focus_report_does_not_hide_the_line_break() {
    let mut events = typed("go\n");
    events.push(Event::FocusGained);
    assert_eq!(take_submit(&mut events), Some(SUBMIT));
    let mut expected = typed("go");
    expected.push(Event::FocusGained);
    assert_eq!(events, expected);
}

#[test]
fn a_trailing_release_does_not_hide_the_line_break() {
    let mut events = typed("go\n");
    let release = Event::Key(KeyEvent::new_with_kind(
        KeyCode::Char('j'),
        KeyModifiers::CONTROL,
        KeyEventKind::Release,
    ));
    events.push(release.clone());
    assert_eq!(take_submit(&mut events), Some(SUBMIT));
    let mut expected = typed("go");
    expected.push(release);
    assert_eq!(events, expected);
}

#[test]
fn shift_enter_and_other_chords_never_submit() {
    for last in [
        KeyEvent::new(KeyCode::Enter, KeyModifiers::SHIFT),
        KeyEvent::new(KeyCode::Enter, KeyModifiers::ALT),
        ctrl('m'),
    ] {
        let mut events = typed("go");
        events.push(Event::Key(last));
        assert_eq!(take_submit(&mut events), None);
    }
}
