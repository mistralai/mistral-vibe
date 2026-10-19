use crossterm::event::{
    Event, KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use tokio::sync::mpsc;

use super::{burst_next, next_input};

fn mouse(kind: MouseEventKind, row: u16) -> Event {
    Event::Mouse(MouseEvent {
        kind,
        column: 1,
        row,
        modifiers: KeyModifiers::NONE,
    })
}

fn key(c: char) -> Event {
    Event::Key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE))
}

fn queue(events: Vec<Event>) -> mpsc::Receiver<Event> {
    let (tx, rx) = mpsc::channel(16);
    for event in events {
        tx.try_send(event).unwrap();
    }
    rx
}

#[test]
fn a_burst_takes_queued_motion_and_wheel_reports() {
    let drag = MouseEventKind::Drag(MouseButton::Left);
    let mut input = queue(vec![
        mouse(drag, 1),
        mouse(MouseEventKind::ScrollDown, 2),
        mouse(MouseEventKind::Moved, 3),
    ]);
    let mut deferred = None;

    assert_eq!(burst_next(&mut input, &mut deferred), Some(mouse(drag, 1)));
    assert_eq!(
        burst_next(&mut input, &mut deferred),
        Some(mouse(MouseEventKind::ScrollDown, 2))
    );
    assert_eq!(
        burst_next(&mut input, &mut deferred),
        Some(mouse(MouseEventKind::Moved, 3))
    );
    assert_eq!(burst_next(&mut input, &mut deferred), None);
    assert_eq!(deferred, None);
}

#[test]
fn a_burst_defers_the_first_other_input_and_leaves_the_rest_queued() {
    let release = mouse(MouseEventKind::Up(MouseButton::Left), 4);
    let mut input = queue(vec![release.clone(), key('a')]);
    let mut deferred = None;

    assert_eq!(burst_next(&mut input, &mut deferred), None);
    assert_eq!(deferred, Some(release));
    assert_eq!(input.try_recv().ok(), Some(key('a')));
}

#[test]
fn deferred_input_is_served_before_newer_input() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .build()
        .unwrap();
    let mut input = queue(vec![key('b')]);
    let mut deferred = Some(key('a'));

    runtime.block_on(async {
        assert_eq!(next_input(&mut input, &mut deferred).await, Some(key('a')));
        assert_eq!(next_input(&mut input, &mut deferred).await, Some(key('b')));
    });
    assert_eq!(deferred, None);
}
