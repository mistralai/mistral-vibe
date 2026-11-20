//! Input typed before the TUI started: a trailing line break submits the drafted text.

use std::time::Duration;

use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};

use crate::terminal_input_filter::TerminalInputFilter;
use crate::terminal_read;

/// Crossterm's tty source never polls with a zero timeout, so queued input needs a nonzero one.
const QUEUED: Duration = Duration::from_millis(1);

/// Read the input already queued when the TUI takes the terminal.
pub fn read(filter: &mut TerminalInputFilter) -> Vec<Event> {
    let mut events = Vec::new();
    while matches!(crossterm::event::poll(QUEUED), Ok(true)) {
        match terminal_read::read() {
            Ok(event) => events.extend(filter.push(event)),
            Err(_) => break,
        }
    }
    events
}

/// The Enter that submits typed-ahead text.
pub const SUBMIT: Event = Event::Key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));

/// Remove the line break ending `events`, returning the Enter that submits them instead.
pub fn take_submit(events: &mut Vec<Event>) -> Option<Event> {
    let index = events
        .iter()
        .rposition(|event| matches!(event, Event::Key(key) if key.kind != KeyEventKind::Release))?;
    let ends_line = matches!(&events[index], Event::Key(key) if is_line_break(key));
    ends_line.then(|| {
        events.remove(index);
        SUBMIT
    })
}

/// A cooked terminal queues Enter as LF, read as Ctrl+J; a Windows console reads it as Enter or Ctrl+Enter.
fn is_line_break(key: &KeyEvent) -> bool {
    match key.code {
        KeyCode::Char('j') => key.modifiers == KeyModifiers::CONTROL,
        KeyCode::Enter => {
            key.modifiers == KeyModifiers::NONE
                || (cfg!(windows) && key.modifiers == KeyModifiers::CONTROL)
        }
        _ => false,
    }
}
