//! Filters terminal reports that Crossterm otherwise exposes as key presses.

use std::time::{Duration, Instant};

use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};

const MAX_REPORT_EVENTS: usize = 64;
const PARTIAL_REPORT_TIMEOUT: Duration = Duration::from_millis(500);

#[derive(Default)]
pub struct TerminalInputFilter {
    pending: Vec<Event>,
    payload: String,
    started: Option<Instant>,
}

impl TerminalInputFilter {
    pub fn push(&mut self, event: Event) -> Vec<Event> {
        if self.pending.is_empty() {
            if is_osc_start(&event) {
                self.pending.push(event);
                self.payload.push(']');
                self.started = Some(Instant::now());
                return Vec::new();
            }
            return vec![event];
        }

        if !is_matching_key(&event) {
            return match event {
                Event::Key(_) => Vec::new(),
                event => vec![event],
            };
        }

        if is_osc_terminator(&event) {
            // Report or not, the buffered bytes are terminal noise, never input.
            self.clear();
            return Vec::new();
        }

        let Some(character) = plain_character(&event) else {
            // A real key (an arrow, Enter, …) interrupts the report: drop the
            // report bytes and deliver the user's key. Replaying the report
            // as input would type its payload into the composer and the
            // user's key would join the junk batch.
            self.clear();
            return vec![event];
        };
        self.payload.push(character);
        let diverged =
            self.pending.len() + 1 >= MAX_REPORT_EVENTS || !is_partial_color_report(&self.payload);
        self.pending.push(event);
        if diverged {
            // The payload diverged from a report: the buffered bytes stay
            // dropped, but this character may be the user's own.
            let event = self.pending.pop().expect("pushed above");
            self.clear();
            return vec![event];
        }
        Vec::new()
    }

    pub fn flush_expired(&mut self, now: Instant) -> Vec<Event> {
        if self
            .started
            .is_some_and(|started| now.duration_since(started) >= PARTIAL_REPORT_TIMEOUT)
        {
            // An unfinished report is terminal noise; it never becomes input.
            self.clear();
        }
        Vec::new()
    }

    fn clear(&mut self) {
        self.pending.clear();
        self.payload.clear();
        self.started = None;
    }
}

fn is_matching_key(event: &Event) -> bool {
    matches!(
        event,
        Event::Key(KeyEvent {
            kind: KeyEventKind::Press | KeyEventKind::Repeat,
            ..
        })
    )
}

fn is_osc_start(event: &Event) -> bool {
    matches!(
        event,
        Event::Key(KeyEvent {
            code: KeyCode::Char(']'),
            modifiers,
            kind: KeyEventKind::Press | KeyEventKind::Repeat,
            ..
        }) if *modifiers == KeyModifiers::ALT
    )
}

fn is_osc_terminator(event: &Event) -> bool {
    matches!(
        event,
        Event::Key(KeyEvent {
            code: KeyCode::Char('g'),
            modifiers,
            kind: KeyEventKind::Press | KeyEventKind::Repeat,
            ..
        }) if *modifiers == KeyModifiers::CONTROL
    ) || matches!(
        event,
        Event::Key(KeyEvent {
            code: KeyCode::Char('\\'),
            modifiers,
            kind: KeyEventKind::Press | KeyEventKind::Repeat,
            ..
        }) if *modifiers == KeyModifiers::ALT
    )
}

fn plain_character(event: &Event) -> Option<char> {
    let Event::Key(key) = event else {
        return None;
    };
    if key.kind == KeyEventKind::Release || !(key.modifiers - KeyModifiers::SHIFT).is_empty() {
        return None;
    }
    match key.code {
        KeyCode::Char(character) => Some(character),
        _ => None,
    }
}

fn is_partial_color_report(payload: &str) -> bool {
    let Some(payload) = payload.strip_prefix(']') else {
        return false;
    };
    let digit_count = payload.chars().take_while(char::is_ascii_digit).count();
    if digit_count == 0 || digit_count > 3 {
        return payload.is_empty();
    }
    if digit_count == payload.len() {
        return true;
    }
    let expected = b";rgb:";
    let remainder = &payload.as_bytes()[digit_count..];
    let shared = remainder.len().min(expected.len());
    if remainder[..shared] != expected[..shared] {
        return false;
    }
    if remainder.len() <= expected.len() {
        return true;
    }
    let color = &remainder[expected.len()..];
    !color.is_empty()
        && color
            .iter()
            .all(|byte| byte.is_ascii_hexdigit() || *byte == b'/')
}
