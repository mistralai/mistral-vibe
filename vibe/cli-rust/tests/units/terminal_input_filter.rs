//! Terminal color reports never become composer key events.

use std::time::{Duration, Instant};

use crossterm::event::{
    Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseEvent, MouseEventKind,
};
use vibe_rs::terminal_input_filter::TerminalInputFilter;

fn key(character: char) -> Event {
    Event::Key(KeyEvent::new(KeyCode::Char(character), KeyModifiers::NONE))
}

fn modified(character: char, modifiers: KeyModifiers) -> Event {
    Event::Key(KeyEvent::new(KeyCode::Char(character), modifiers))
}

fn release(character: char, modifiers: KeyModifiers) -> Event {
    Event::Key(KeyEvent::new_with_kind(
        KeyCode::Char(character),
        modifiers,
        KeyEventKind::Release,
    ))
}

fn mouse_move() -> Event {
    Event::Mouse(MouseEvent {
        kind: MouseEventKind::Moved,
        column: 1,
        row: 1,
        modifiers: KeyModifiers::NONE,
    })
}

fn report_events(terminator: Event) -> Vec<Event> {
    let mut events = vec![modified(']', KeyModifiers::ALT)];
    events.extend("11;rgb:1e1e/1e1e/2e2e".chars().map(key));
    events.push(terminator);
    events
}

#[test]
fn drops_bell_terminated_color_report() {
    let mut filter = TerminalInputFilter::default();
    let output: Vec<_> = report_events(modified('g', KeyModifiers::CONTROL))
        .into_iter()
        .flat_map(|event| filter.push(event))
        .collect();

    assert!(output.is_empty());
}

#[test]
fn drops_string_terminated_color_report_and_keeps_following_key() {
    let mut filter = TerminalInputFilter::default();
    let mut output: Vec<_> = report_events(modified('\\', KeyModifiers::ALT))
        .into_iter()
        .flat_map(|event| filter.push(event))
        .collect();
    output.extend(filter.push(key('x')));

    assert_eq!(output, [key('x')]);
}

#[test]
fn releases_alt_bracket_when_the_sequence_is_not_a_color_report() {
    let mut filter = TerminalInputFilter::default();
    let start = modified(']', KeyModifiers::ALT);

    assert!(filter.push(start.clone()).is_empty());
    assert_eq!(filter.push(key('x')), [start, key('x')]);
}

#[test]
fn releases_non_ascii_color_candidate_without_panicking() {
    let mut filter = TerminalInputFilter::default();
    let mut events = vec![modified(']', KeyModifiers::ALT)];
    events.extend("1;rgbé".chars().map(key));

    let output: Vec<_> = events
        .iter()
        .cloned()
        .flat_map(|event| filter.push(event))
        .collect();

    assert_eq!(output, events);
}

#[test]
fn drops_color_report_interleaved_with_releases_and_mouse() {
    let mut filter = TerminalInputFilter::default();
    let mut events = vec![
        modified(']', KeyModifiers::ALT),
        release(']', KeyModifiers::ALT),
        mouse_move(),
        Event::Resize(80, 24),
        Event::FocusLost,
        Event::FocusGained,
    ];
    events.extend(
        "11;rgb:1e1e/1e1e/2e2e"
            .chars()
            .flat_map(|character| [key(character), release(character, KeyModifiers::NONE)]),
    );
    events.push(modified('g', KeyModifiers::CONTROL));

    let output: Vec<_> = events
        .into_iter()
        .flat_map(|event| filter.push(event))
        .collect();

    assert_eq!(
        output,
        [
            mouse_move(),
            Event::Resize(80, 24),
            Event::FocusLost,
            Event::FocusGained,
        ]
    );
}

#[test]
fn holds_the_match_across_noise_then_releases_on_a_real_key() {
    let mut filter = TerminalInputFilter::default();
    let start = modified(']', KeyModifiers::ALT);

    assert!(filter.push(start.clone()).is_empty());
    assert!(filter.push(release(']', KeyModifiers::ALT)).is_empty());
    assert_eq!(filter.push(mouse_move()), [mouse_move()]);
    assert_eq!(filter.push(key('x')), [start, key('x')]);
}

#[test]
fn releases_an_incomplete_candidate_after_the_bound() {
    let mut filter = TerminalInputFilter::default();
    let start = modified(']', KeyModifiers::ALT);

    assert!(filter.push(start.clone()).is_empty());
    assert_eq!(
        filter.flush_expired(Instant::now() + Duration::from_secs(1)),
        [start]
    );
}
