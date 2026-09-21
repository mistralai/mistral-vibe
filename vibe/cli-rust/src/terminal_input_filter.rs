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
            if is_color_report(&self.payload) {
                self.clear();
                return Vec::new();
            }
            return self.release_with(event);
        }

        let Some(character) = plain_character(&event) else {
            return self.release_with(event);
        };
        self.payload.push(character);
        self.pending.push(event);
        if self.pending.len() >= MAX_REPORT_EVENTS || !is_partial_color_report(&self.payload) {
            return self.release();
        }
        Vec::new()
    }

    pub fn flush_expired(&mut self, now: Instant) -> Vec<Event> {
        if self
            .started
            .is_some_and(|started| now.duration_since(started) >= PARTIAL_REPORT_TIMEOUT)
        {
            return self.release();
        }
        Vec::new()
    }

    fn release_with(&mut self, event: Event) -> Vec<Event> {
        self.pending.push(event);
        self.release()
    }

    fn release(&mut self) -> Vec<Event> {
        let pending = std::mem::take(&mut self.pending);
        self.payload.clear();
        self.started = None;
        pending
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

fn is_color_report(payload: &str) -> bool {
    let Some(color) = color_payload(payload) else {
        return false;
    };
    let Some((red, remainder)) = color.split_once('/') else {
        return false;
    };
    let Some((green, blue)) = remainder.split_once('/') else {
        return false;
    };
    [red, green, blue].into_iter().all(|component| {
        !component.is_empty() && component.bytes().all(|byte| byte.is_ascii_hexdigit())
    })
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

fn color_payload(payload: &str) -> Option<&str> {
    let payload = payload.strip_prefix(']')?;
    let digit_count = payload.chars().take_while(char::is_ascii_digit).count();
    if !(1..=3).contains(&digit_count) {
        return None;
    }
    payload[digit_count..].strip_prefix(";rgb:")
}
