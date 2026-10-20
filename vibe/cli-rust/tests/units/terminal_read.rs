//! AltGr characters (reported as Ctrl+Alt on Windows) type as text; other Ctrl+Alt chords stay.

use std::time::Instant;

use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use vibe_rs::chat_input::Action;
use vibe_rs::key_burst::{KeyBurst, PASTE_GAP};
use vibe_rs::keymap::action_for;
use vibe_rs::terminal_read::{normalize, normalize_on};

const ALTGR: KeyModifiers = KeyModifiers::CONTROL.union(KeyModifiers::ALT);

/// The AltGr layer of a Polish/German-like layout.
fn polish_altgr(c: char) -> bool {
    matches!(c, 'ą' | 'Ą' | '@' | '{')
}

fn key(code: KeyCode, modifiers: KeyModifiers) -> Event {
    Event::Key(KeyEvent::new(code, modifiers))
}

fn typed(event: Event) -> Option<Action> {
    let Event::Key(key) = event else {
        panic!("expected a key event");
    };
    action_for(&key)
}

#[test]
fn altgr_character_inserts_text() {
    let event = normalize_on(key(KeyCode::Char('ą'), ALTGR), polish_altgr);

    assert_eq!(event, key(KeyCode::Char('ą'), KeyModifiers::NONE));
    assert_eq!(typed(event), Some(Action::Insert('ą')));
}

#[test]
fn shifted_altgr_character_keeps_shift() {
    let event = normalize_on(
        key(KeyCode::Char('Ą'), ALTGR | KeyModifiers::SHIFT),
        polish_altgr,
    );

    assert_eq!(event, key(KeyCode::Char('Ą'), KeyModifiers::SHIFT));
    assert_eq!(typed(event), Some(Action::Insert('Ą')));
}

#[test]
fn altgr_ascii_punctuation_inserts_text() {
    for c in ['@', '{'] {
        let event = normalize_on(key(KeyCode::Char(c), ALTGR), polish_altgr);
        assert_eq!(typed(event), Some(Action::Insert(c)));
    }
}

#[test]
fn altgr_digit_inserts_text_when_the_layout_types_it() {
    let event = normalize_on(key(KeyCode::Char('4'), ALTGR), |c| c == '4');

    assert_eq!(typed(event), Some(Action::Insert('4')));
}

#[test]
fn altgr_release_keeps_its_kind() {
    let release = Event::Key(KeyEvent::new_with_kind(
        KeyCode::Char('@'),
        ALTGR,
        KeyEventKind::Release,
    ));

    let expected = Event::Key(KeyEvent::new_with_kind(
        KeyCode::Char('@'),
        KeyModifiers::NONE,
        KeyEventKind::Release,
    ));
    assert_eq!(normalize_on(release, polish_altgr), expected);
}

/// crossterm falls back to the key's base character for an AltGr dead key (AZERTY AltGr+é is `~`).
#[test]
fn base_layer_character_keeps_ctrl_alt() {
    for c in ['é', 'c', 'с', '4'] {
        let event = key(KeyCode::Char(c), ALTGR);
        assert_eq!(normalize_on(event.clone(), polish_altgr), event);
        assert_eq!(typed(event), None);
    }
}

#[test]
fn single_modifier_shortcuts_are_untouched() {
    for modifiers in [KeyModifiers::CONTROL, KeyModifiers::ALT] {
        let event = key(KeyCode::Char('ą'), modifiers);
        assert_eq!(normalize_on(event.clone(), polish_altgr), event);
    }
}

/// Conhost pastes `zażółć` on a Polish layout as press/release pairs, AltGr letters with Ctrl+Alt.
fn polish_paste(on_altgr_layer: fn(char) -> bool) -> Vec<Event> {
    "zażółć"
        .chars()
        .flat_map(|c| {
            let modifiers = if on_altgr_layer(c) {
                ALTGR
            } else {
                KeyModifiers::NONE
            };
            [
                KeyEvent::new(KeyCode::Char(c), modifiers),
                KeyEvent::new_with_kind(KeyCode::Char(c), modifiers, KeyEventKind::Release),
            ]
        })
        .map(Event::Key)
        .collect()
}

fn coalesce(events: Vec<Event>) -> Vec<Event> {
    let mut bursts = KeyBurst::default();
    let now = Instant::now();
    let mut out: Vec<Event> = events
        .into_iter()
        .flat_map(|event| bursts.push(vec![event], now))
        .collect();
    out.extend(bursts.push(Vec::new(), now + PASTE_GAP));
    out
}

#[test]
fn normalized_altgr_paste_coalesces_into_one_paste() {
    let polish = |c| "ąćęłńóśźż".contains(c);
    let events = polish_paste(polish)
        .into_iter()
        .map(|event| normalize_on(event, polish))
        .collect();

    assert_eq!(coalesce(events), vec![Event::Paste("zażółć".into())]);
}

#[test]
fn ctrl_alt_on_non_character_keys_is_untouched() {
    let event = key(KeyCode::Backspace, ALTGR);

    assert_eq!(normalize_on(event.clone(), |_| true), event);
}

#[cfg(not(windows))]
#[test]
fn other_platforms_keep_ctrl_alt_characters() {
    let event = key(KeyCode::Char('ą'), ALTGR);

    assert_eq!(normalize(event.clone()), event);
}
