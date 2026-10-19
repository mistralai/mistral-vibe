//! OSC 7501 revision 0.3 detection and legacy presentation fallback.

use std::time::{Duration, Instant};

use crossterm::event::{Event, KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::program_status::detection::Detection;
use vibe_rs::program_status::{BlockedKind, ProgramState};
use vibe_rs::terminal_input_filter::TerminalInputFilter;
use vibe_rs::terminal_notifier::{NotificationContext, TerminalNotifier};

fn key(character: char, modifiers: KeyModifiers) -> Event {
    Event::Key(KeyEvent::new(KeyCode::Char(character), modifiers))
}

fn reply(payload: &str, terminator: Event) -> TerminalInputFilter {
    let mut filter = TerminalInputFilter::default();
    let events = [key(']', KeyModifiers::ALT)]
        .into_iter()
        .chain(payload.chars().map(|ch| key(ch, KeyModifiers::NONE)))
        .chain([terminator]);
    for event in events {
        assert!(filter.push(event).is_empty());
    }
    assert_eq!(
        filter.push(key('x', KeyModifiers::NONE)),
        [key('x', KeyModifiers::NONE)]
    );
    filter
}

fn confirmed_notifier() -> TerminalNotifier {
    let mut notifier = TerminalNotifier::default();
    notifier.configure(true, true);
    notifier.enable_program_reports(true);
    let mut filter = reply("7501;?", key('\\', KeyModifiers::ALT));
    notifier
        .program_status_detection
        .observe(&mut filter, Some(Instant::now() + Duration::from_secs(1)));
    notifier
}

#[test]
fn accepts_both_terminators_and_ignores_future_fields_with_bounded_storage() {
    // https://www.superlogical.com/rex/docs/build/program-status, revision 0.3 Feature detection.
    for terminator in [
        key('\\', KeyModifiers::ALT),
        key('g', KeyModifiers::CONTROL),
    ] {
        let detection = Detection::default();
        let mut filter = reply(&format!("7501;?:future={}", "x".repeat(4096)), terminator);
        detection.observe(&mut filter, Some(Instant::now() + Duration::from_secs(1)));
        assert!(detection.supported());
        assert!(!filter.take_program_status_reply());
    }
}

#[test]
fn late_and_unsolicited_replies_do_not_confirm_support() {
    for deadline in [None, Some(Instant::now() - Duration::from_secs(1))] {
        let detection = Detection::default();
        let mut filter = reply("7501;?", key('\\', KeyModifiers::ALT));
        detection.observe(&mut filter, deadline);
        assert!(!detection.supported());
    }
}

#[test]
fn color_and_incomplete_replies_do_not_confirm_support() {
    let detection = Detection::default();
    let deadline = Some(Instant::now() + Duration::from_secs(1));
    let mut filter = reply("11;rgb:ffff/ffff/ffff", key('g', KeyModifiers::CONTROL));
    detection.observe(&mut filter, deadline);
    assert!(!detection.supported());
    filter.push(key(']', KeyModifiers::ALT));
    for ch in "7501;?".chars() {
        assert!(filter.push(key(ch, KeyModifiers::NONE)).is_empty());
    }
    filter.flush_expired(Instant::now() + Duration::from_secs(1));
    detection.observe(&mut filter, deadline);
    assert!(!detection.supported());
}

#[test]
fn confirmed_support_keeps_session_title_and_suppresses_status_and_bells() {
    let mut notifier = confirmed_notifier();
    notifier.set_default_title("Session title");
    notifier.clear_outcome();
    assert_eq!(
        notifier.take_title(ProgramState::Working).as_deref(),
        Some("Session title")
    );
    assert!(notifier
        .take_program_report(ProgramState::Working, "")
        .unwrap()
        .contains("state=working"));
    notifier.set_focus(false);
    for context in [
        NotificationContext::ActionRequired,
        NotificationContext::Complete,
        NotificationContext::Failed,
    ] {
        notifier.notify(context, Instant::now());
        assert_eq!(notifier.title(ProgramState::Working), "Session title");
        assert!(!notifier.take_bell());
    }
    notifier.set_default_title("Renamed");
    assert_eq!(
        notifier.take_title(ProgramState::Working).as_deref(),
        Some("Renamed")
    );
}

#[test]
fn confirmation_removes_a_pending_legacy_indicator_and_bell() {
    let mut notifier = TerminalNotifier::default();
    notifier.configure(true, true);
    notifier.enable_program_reports(true);
    notifier.set_focus(false);
    notifier.notify(NotificationContext::ActionRequired, Instant::now());
    assert_eq!(
        notifier
            .take_title(ProgramState::Blocked(BlockedKind::Permission))
            .as_deref(),
        Some("Vibe - Action Required")
    );
    let mut filter = reply("7501;?", key('\\', KeyModifiers::ALT));
    notifier
        .program_status_detection
        .observe(&mut filter, Some(Instant::now() + Duration::from_secs(1)));
    assert!(!notifier.take_bell());
    assert_eq!(
        notifier.take_title(ProgramState::Working).as_deref(),
        Some("Vibe")
    );
    notifier.configure(true, false);
    assert_eq!(
        notifier.take_title(ProgramState::Working).as_deref(),
        Some("Vibe")
    );
}

#[test]
fn disabling_reports_restores_legacy_notifications() {
    let mut notifier = confirmed_notifier();
    notifier.take_program_report(ProgramState::Working, "");
    notifier.configure(true, false);
    assert!(notifier
        .take_program_report(ProgramState::Working, "")
        .unwrap()
        .contains("state=idle"));
    notifier.set_focus(false);
    notifier.notify(NotificationContext::Complete, Instant::now());
    assert_eq!(
        notifier.title(ProgramState::Working),
        "Vibe - Task Complete"
    );
    assert!(notifier.take_bell());
    notifier.configure(true, true);
    notifier.enable_program_reports(false);
    notifier.clear_outcome();
    assert_eq!(notifier.title(ProgramState::Working), ">> Vibe");
}
