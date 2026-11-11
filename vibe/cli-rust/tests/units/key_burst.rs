//! Raw-key pastes coalesce into one paste, typing stays key by key, CR and LF both break lines.

use std::time::{Duration, Instant};

use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::input::handle_paste;
use vibe_rs::key_burst::{KeyBurst, KEY_GAP, PASTE_GAP};
use vibe_rs::{config, config_edit, config_fields};

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
        '\r' | '\n' => KeyCode::Enter,
        character => KeyCode::Char(character),
    }
}

/// ConPTY reads a CR as Enter and an LF as Ctrl+Enter.
fn console_key(character: char, kind: KeyEventKind) -> Event {
    let modifiers = if character == '\n' {
        KeyModifiers::CONTROL
    } else {
        KeyModifiers::NONE
    };
    Event::Key(KeyEvent::new_with_kind(code(character), modifiers, kind))
}

/// The press/release pairs crossterm reads from a Windows console paste.
fn windows_paste(text: &str) -> Vec<Event> {
    text.chars()
        .flat_map(|character| {
            [
                console_key(character, KeyEventKind::Press),
                console_key(character, KeyEventKind::Release),
            ]
        })
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

const LINE_ENDINGS: [&str; 3] = ["line1\rline2", "line1\nline2", "line1\r\nline2"];

#[test]
fn windows_multiline_paste_becomes_one_paste() {
    for text in LINE_ENDINGS {
        let mut bursts = KeyBurst::default();

        let out = feed_one_by_one(&mut bursts, windows_paste(text), Instant::now());

        assert_eq!(out, vec![Event::Paste(text.into())], "{text:?}");
    }
}

#[test]
fn paste_targets_read_every_line_ending_as_a_line_break() {
    for text in LINE_ENDINGS {
        let mut app = App::default();
        app.config_screen.open = true;
        let fields = json!({
            "fields": [{"name": "prompt", "path": "/prompt", "kind": "str", "value": ""}],
            "targets": ["user-toml"],
        });
        config::apply_loaded(&mut app, config_fields::parse(&fields));
        let field = config::filtered(&app)[0].clone();
        config_edit::open(&mut app, field);

        handle_paste(&mut app, text.into());
        config_edit::paste(&mut app, text.into());

        assert_eq!(app.chat_input.input, "line1\nline2", "{text:?}");
        let draft = &app.config_screen.edit.as_ref().unwrap().draft;
        assert_eq!(draft, "line1\nline2", "{text:?}");
    }
}

#[test]
fn typing_stays_key_by_key_and_enter_submits() {
    let mut bursts = KeyBurst::default();
    let start = Instant::now();
    let mut out = Vec::new();

    for (index, character) in "hi\r".chars().enumerate() {
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
    for enter in ['\r', '\n'] {
        let mut bursts = KeyBurst::default();
        let text = format!("a{enter}");

        let out = feed_one_by_one(&mut bursts, windows_paste(&text), Instant::now());

        let enter = console_key(enter, KeyEventKind::Press);
        assert_eq!(out, vec![press(code('a')), enter]);
    }
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
