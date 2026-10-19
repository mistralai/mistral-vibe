//! Coalesces a paste typed in as raw key presses into one paste event.

use std::time::{Duration, Instant};

use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};

/// Keys this close together come from the terminal, never from a typist.
pub const KEY_GAP: Duration = Duration::from_millis(10);
/// Once a burst is a paste, the next key may lag this long (Windows chunks pastes).
pub const PASTE_GAP: Duration = Duration::from_millis(60);
const PASTE_KEYS: usize = 3;

#[derive(Default)]
pub struct KeyBurst {
    held: Vec<KeyEvent>,
    text: String,
    deadline: Option<Instant>,
}

impl KeyBurst {
    /// When the held keys flush if no other key arrives.
    pub fn deadline(&self) -> Option<Instant> {
        self.deadline
    }

    /// Buffer `events`, returning the events ready to deliver, in order.
    pub fn push(&mut self, events: Vec<Event>, now: Instant) -> Vec<Event> {
        let mut out = Vec::new();
        if self.deadline.is_some_and(|deadline| now >= deadline) {
            self.flush(&mut out);
        }
        for event in events {
            if let Event::Key(key) = &event {
                if let Some(character) = text_char(key) {
                    self.hold(*key, character, now);
                    continue;
                }
                // Windows reports a release after every pasted key.
                if key.kind == KeyEventKind::Release && !self.held.is_empty() {
                    continue;
                }
            }
            self.flush(&mut out);
            out.push(event);
        }
        out
    }

    fn hold(&mut self, key: KeyEvent, character: char, now: Instant) {
        self.held.push(key);
        self.text.push(character);
        let gap = if self.held.len() >= PASTE_KEYS {
            PASTE_GAP
        } else {
            KEY_GAP
        };
        self.deadline = Some(now + gap);
    }

    fn flush(&mut self, out: &mut Vec<Event>) {
        self.deadline = None;
        if self.held.len() >= PASTE_KEYS {
            self.held.clear();
            out.push(Event::Paste(std::mem::take(&mut self.text)));
            return;
        }
        self.text.clear();
        out.extend(self.held.drain(..).map(Event::Key));
    }
}

fn text_char(key: &KeyEvent) -> Option<char> {
    if key.kind == KeyEventKind::Release || !(key.modifiers - KeyModifiers::SHIFT).is_empty() {
        return None;
    }
    match key.code {
        KeyCode::Char(character) => Some(character),
        KeyCode::Enter => Some('\n'),
        KeyCode::Tab => Some('\t'),
        _ => None,
    }
}
