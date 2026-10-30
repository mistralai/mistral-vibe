//! Raw-key pastes coalesce into one paste event; typing stays key by key.

use std::time::{Duration, Instant};

use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use vibe_rs::key_burst::{KeyBurst, KEY_GAP, PASTE_GAP};

use crate::support::{ctrl, key};

fn press(code: KeyCode) -> Event {
    Event::Key(key(code))
}

fn release(code: KeyCode) -> Event {
    Event::Key(KeyEvent::new_with_kind(
        code,
        KeyModifiers::NONE,
        KeyEventKind::Release,
    ))
}

fn code(character: char) -> KeyCode {
    match character {
        '\n' => KeyCode::Enter,
        character => KeyCode::Char(character),
    }
}

/// The press/release pairs crossterm reads from a Windows console paste.
fn windows_paste(text: &str) -> Vec<Event> {
    text.chars()
        .flat_map(|character| [press(code(character)), release(code(character))])
        .collect()
}

fn feed_one_by_one(bursts: &mut KeyBurst, events: Vec<Event>, now: Instant) -> Vec<Event> {
    let mut out: Vec<Event> = events
        .into_iter()
        .flat_map(|event| bursts.push(vec![event], now))
        .collect();
    out.extend(bursts.push(Vec::new(), now + PASTE_GAP));
    out
}

#[test]
fn windows_multiline_paste_becomes_one_paste() {
    let mut bursts = KeyBurst::default();

    let out = feed_one_by_one(&mut bursts, windows_paste("line1\nline2\n"), Instant::now());

    assert_eq!(out, vec![Event::Paste("line1\nline2\n".into())]);
}

#[test]
fn typing_stays_key_by_key_and_enter_submits() {
    let mut bursts = KeyBurst::default();
    let start = Instant::now();
    let mut out = Vec::new();

    for (index, character) in "hi\n".chars().enumerate() {
        let at = start + KEY_GAP * 3 * index as u32;
        out.extend(bursts.push(vec![press(code(character))], at));
        out.extend(bursts.push(vec![release(code(character))], at + KEY_GAP * 2));
    }
    out.extend(bursts.push(Vec::new(), start + Duration::from_secs(1)));

    let presses: Vec<_> = out
        .into_iter()
        .filter(|event| matches!(event, Event::Key(key) if key.kind == KeyEventKind::Press))
        .collect();
    assert_eq!(
        presses,
        vec![press(code('h')), press(code('i')), press(KeyCode::Enter)]
    );
}

#[test]
fn short_bursts_stay_keys() {
    let mut bursts = KeyBurst::default();

    let out = feed_one_by_one(&mut bursts, windows_paste("a\n"), Instant::now());

    assert_eq!(out, vec![press(code('a')), press(KeyCode::Enter)]);
}

#[test]
fn slow_windows_chunk_joins_an_open_paste() {
    let mut bursts = KeyBurst::default();
    let start = Instant::now();

    let mut out = bursts.push(windows_paste("abc"), start);
    out.extend(bursts.push(windows_paste("\nd"), start + PASTE_GAP / 2));
    out.extend(bursts.push(Vec::new(), start + PASTE_GAP * 2));

    assert_eq!(out, vec![Event::Paste("abc\nd".into())]);
}

#[test]
fn non_text_key_flushes_the_burst_first() {
    let mut bursts = KeyBurst::default();
    let mut events = windows_paste("abc");
    events.push(press(KeyCode::Up));

    let out = bursts.push(events, Instant::now());

    assert_eq!(out, vec![Event::Paste("abc".into()), press(KeyCode::Up)]);
    assert_eq!(bursts.deadline(), None);
}

#[test]
fn shortcuts_are_never_absorbed() {
    let mut bursts = KeyBurst::default();
    let ctrl_c = Event::Key(ctrl('c'));

    let out = bursts.push(vec![ctrl_c.clone()], Instant::now());

    assert_eq!(out, vec![ctrl_c]);
    assert_eq!(bursts.deadline(), None);
}
