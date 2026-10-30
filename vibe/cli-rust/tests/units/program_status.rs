//! Protocol limits and reports through the real application handlers.

use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::json;
use std::time::Instant;
use vibe_rs::app::{App, Status};
use vibe_rs::program_status::{encode_report, BlockedKind, ProgramState};
use vibe_rs::server::notification;
use vibe_rs::terminal_notifier::{NotificationContext, TerminalNotifier};

use crate::support::notify;

fn notifier() -> TerminalNotifier {
    let mut notifier = TerminalNotifier::default();
    notifier.configure(true, true);
    notifier.enable_program_reports(true);
    notifier
}

#[test]
fn reports_use_standard_base64_and_repeat_the_app_name() {
    assert_eq!(
        encode_report(ProgramState::Blocked(BlockedKind::Permission), "Approve?"),
        "\x1b]7501;state=blocked:kind=permission:app=vibe:msg=QXBwcm92ZT8=\x1b\\"
    );
    assert_eq!(
        encode_report(ProgramState::Blocked(BlockedKind::Question), "Café?"),
        "\x1b]7501;state=blocked:kind=question:app=vibe:msg=Q2Fmw6k/\x1b\\"
    );
    for (state, name) in [
        (ProgramState::Idle, "idle"),
        (ProgramState::Working, "working"),
        (ProgramState::Done, "done"),
        (ProgramState::Error, "error"),
    ] {
        assert_eq!(
            encode_report(state, ""),
            format!("\x1b]7501;state={name}:app=vibe\x1b\\")
        );
    }
}

#[test]
fn messages_remove_controls_and_fit_the_utf8_and_sequence_limits() {
    let controls: String = (0..=31)
        .chain(127..=159)
        .map(|n| char::from_u32(n).unwrap())
        .collect();
    assert_eq!(
        encode_report(ProgramState::Done, &format!("a{controls}b")),
        "\x1b]7501;state=done:app=vibe:msg=YSAgIGI=\x1b\\"
    );
    for message in [format!("{}€z", "a".repeat(2046)), "é".repeat(2048)] {
        let report = encode_report(ProgramState::Blocked(BlockedKind::Question), &message);
        assert!(report.len() <= 4096);
        let encoded = report
            .split(":msg=")
            .nth(1)
            .unwrap()
            .strip_suffix("\x1b\\")
            .unwrap();
        assert!(encoded.len() <= 2732);
        let decoded = STANDARD.decode(encoded).unwrap();
        assert!(decoded.len() <= 2048);
        assert!(std::str::from_utf8(&decoded).is_ok());
    }
}

#[test]
fn focus_does_not_resend_but_title_changes_and_resume_do() {
    let mut n = notifier();
    let state = ProgramState::Working;
    n.take_program_report(state, "").unwrap();
    n.set_focus(false);
    n.set_default_title("Renamed");
    let working = n.take_program_report(state, "").unwrap();
    assert_eq!(working, encode_report(ProgramState::Working, "Renamed"));
    assert!(n.take_program_report(state, "").is_none());
    n.invalidate_program_status();
    assert_eq!(n.take_program_report(state, ""), Some(working));
    n.configure(false, false);
    assert_eq!(
        n.take_program_report(state, "").as_deref(),
        Some("\x1b]7501;state=idle:app=vibe\x1b\\")
    );
    assert!(n.take_program_report(state, "").is_none());
    assert!(n.take_shutdown_report().is_none());
    n.configure(false, true);
    n.take_program_report(state, "");
    assert!(n.take_shutdown_report().is_some());
    assert!(n.take_shutdown_report().is_none());
}

#[test]
fn disabled_outputs_never_emit_reports_or_reset_a_record() {
    let mut n = notifier();
    n.enable_program_reports(false);
    let state = ProgramState::Working;
    assert!(n.take_program_report(state, "").is_none());
    assert!(n.take_shutdown_report().is_none());
}

#[test]
fn completed_failed_and_interrupted_turns_report_distinct_states() {
    for (status, state) in [
        ("completed", "done"),
        ("failed", "error"),
        ("interrupted", "idle"),
    ] {
        let mut app = App {
            terminal_notifier: notifier(),
            ..Default::default()
        };
        app.set_status(Status::Generating {
            since: Instant::now(),
        });
        notify(
            &mut app,
            notification::TURN_COMPLETED,
            json!({"turn":{"status":status}}),
        );
        assert_eq!(
            vibe_rs::terminal_notifier::take_program_report(&mut app),
            Some(encode_report(
                match state {
                    "done" => ProgramState::Done,
                    "error" => ProgramState::Error,
                    _ => ProgramState::Idle,
                },
                match state {
                    "done" => "Vibe",
                    "error" => "Turn failed",
                    _ => "",
                }
            ))
        );
    }
}

#[test]
fn approval_takes_precedence_and_dismissal_restores_the_question() {
    let mut app = App {
        terminal_notifier: notifier(),
        ..Default::default()
    };
    app.set_status(Status::Generating {
        since: Instant::now(),
    });
    vibe_rs::question_app::on_callback_call(
        &mut app,
        &json!({"callback":{"callbackId":"q","detail":{"kind":"user_input","request":{"questions":[{"question":"Pick?","options":[]}]}}}}),
    );
    let mut approval = vibe_rs::server::ApprovalCallback {
        address: vibe_rs::server::CallbackAddress {
            callback_id: "a".into(),
            ..Default::default()
        },
        ..Default::default()
    };
    approval.detail.effect.display.status_text = "Approve?".into();
    app.approval.enqueue(approval).unwrap();
    vibe_rs::approval::show_pending(&mut app);
    let blocked = encode_report(ProgramState::Blocked(BlockedKind::Permission), "Approve?");
    assert_eq!(
        vibe_rs::terminal_notifier::take_program_report(&mut app),
        Some(blocked.clone())
    );
    notify(
        &mut app,
        notification::TURN_COMPLETED,
        json!({"turn":{"status":"failed"}}),
    );
    assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
    vibe_rs::approval::apply_event(
        &mut app,
        vibe_rs::approval::Event::Responded {
            callback_id: "a".into(),
        },
    );
    assert_eq!(
        vibe_rs::terminal_notifier::take_program_report(&mut app),
        Some(encode_report(
            ProgramState::Blocked(BlockedKind::Question),
            "Pick?"
        ))
    );
    vibe_rs::question_app::dismiss(
        &mut app,
        &vibe_rs::question_app::QuestionSource::Callback(vibe_rs::server::CallbackAddress {
            callback_id: "q".into(),
            ..Default::default()
        }),
    );
    assert_eq!(
        vibe_rs::terminal_notifier::take_program_report(&mut app),
        Some(encode_report(ProgramState::Error, "Turn failed"))
    );
}

#[test]
fn server_crash_reports_error_without_changing_notification_bells() {
    let mut app = App {
        terminal_notifier: notifier(),
        ..Default::default()
    };
    app.set_status(Status::Generating {
        since: Instant::now(),
    });
    assert!(vibe_rs::event_loop::notifications::surface_server_close(
        &mut app, true
    ));
    assert_eq!(
        vibe_rs::terminal_notifier::take_program_report(&mut app),
        Some(encode_report(
            ProgramState::Error,
            "the app server exited unexpectedly"
        ))
    );
    assert!(!app.terminal_notifier.take_bell());
    assert!(matches!(app.session.status, Status::Failed));
}

#[test]
fn failure_reports_keep_the_existing_completion_title_and_bell() {
    let mut n = notifier();
    n.set_focus(false);
    let state = ProgramState::Error;
    n.notify(NotificationContext::Failed, Instant::now());
    assert_eq!(n.title(ProgramState::Error), "Vibe - Task Complete");
    assert!(n.take_bell());
    assert_eq!(
        n.take_program_report(state, ""),
        Some(encode_report(ProgramState::Error, "Task failed"))
    );
}

#[test]
fn shell_effect_outcome_survives_a_successful_rpc() {
    // _shell.py::shell_effect_state and _unified_harness_backend_adapter.py::_dispatch_shell_command.
    for (status, outcome) in [
        ("completed", ProgramState::Done),
        ("failed", ProgramState::Error),
        ("cancelled", ProgramState::Idle),
    ] {
        let mut app = App::default();
        app.set_status(Status::Ready);
        notify(
            &mut app,
            notification::HISTORY_ENTRY_ADDED,
            json!({"entry":{
                "id":"shell-1", "type":"effect", "state":{"status":status},
                "detail":{"kind":"shell"}
            }}),
        );
        assert_eq!(
            vibe_rs::program_status::shell_outcome(&app, "shell-1", false),
            Some(outcome)
        );
    }
    let app = App::default();
    assert_eq!(
        vibe_rs::program_status::shell_outcome(&app, "missing", true),
        Some(ProgramState::Error)
    );
}

#[test]
fn title_and_error_sources_are_sanitized_and_message_changes_resend() {
    let mut n = notifier();
    n.set_default_title(&format!("a\x1b{}€", "é".repeat(1023)));
    for state in [
        ProgramState::Working,
        ProgramState::Done,
        ProgramState::Error,
    ] {
        n.set_error_message(&format!("a\x1b{}€\nDetails", "é".repeat(1023)));
        n.remember_completion(state);
        let report = n.take_program_report(state, "").unwrap();
        assert_eq!(
            report,
            encode_report(state, &format!("a{}", "é".repeat(1023)))
        );
        assert!(report.len() < 4096);
        assert!(n.take_program_report(state, "").is_none());
    }
    let state = ProgramState::Error;
    n.set_error_message("Different failure\nDetails");
    assert_eq!(
        n.take_program_report(state, ""),
        Some(encode_report(ProgramState::Error, "Different failure"))
    );
    let state = ProgramState::Idle;
    n.clear_outcome();
    assert_eq!(
        n.take_program_report(state, ""),
        Some(encode_report(ProgramState::Idle, ""))
    );
}
