//! Status survives session transitions and either shell event order.

use serde_json::json;
use std::time::Instant;
use vibe_rs::app::{App, Status};
use vibe_rs::program_status::{self, encode_report, BlockedKind, ProgramState};
use vibe_rs::server::notification;

use crate::support::notify;

fn app() -> App {
    let mut app = App::default();
    app.terminal_notifier.configure(true, true);
    app.terminal_notifier.enable_program_reports(true);
    app.set_session_id("session".into());
    app.set_status(Status::Ready);
    app
}

fn report(app: &mut App, state: ProgramState, message: &str) {
    assert_eq!(
        vibe_rs::terminal_notifier::take_program_report(app),
        Some(encode_report(
            state,
            if !message.is_empty() {
                message
            } else {
                match state {
                    ProgramState::Working | ProgramState::Done => "Vibe",
                    ProgramState::Error => "Task failed",
                    _ => "",
                }
            }
        ))
    );
}
#[test]
fn changing_session_resets_the_emitted_state_immediately() {
    for state in [
        ProgramState::Done,
        ProgramState::Error,
        ProgramState::Working,
    ] {
        let mut app = app();
        if state == ProgramState::Working {
            app.session.shell_operation_id = Some("shell".into());
        } else {
            app.terminal_notifier.remember_completion(state);
        }
        report(&mut app, state, "");
        app.set_session_id("session".into());
        assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
        app.set_session_id("new-session".into());
        report(&mut app, ProgramState::Idle, "");
        app.set_status(Status::Ready);
        assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
    }
}
#[test]
fn status_transitions_preserve_open_dialogs_and_their_messages() {
    for permission in [false, true] {
        let mut app = app();
        if permission {
            let mut callback = vibe_rs::server::ApprovalCallback {
                address: vibe_rs::server::CallbackAddress {
                    callback_id: "a".into(),
                    ..Default::default()
                },
                ..Default::default()
            };
            callback.detail.effect.display.status_text = "Approve?".into();
            app.approval.enqueue(callback).unwrap();
            vibe_rs::approval::show_pending(&mut app);
        } else {
            vibe_rs::question_app::on_callback_call(
                &mut app,
                &json!({
                    "callback":{"callbackId":"q","detail":{"kind":"user_input","request":{
                        "questions":[{"question":"Pick?","options":[]}]
                    }}}
                }),
            );
        }
        let kind = if permission {
            BlockedKind::Permission
        } else {
            BlockedKind::Question
        };
        report(
            &mut app,
            ProgramState::Blocked(kind),
            if permission { "Approve?" } else { "Pick?" },
        );
        for status in [
            Status::Ready,
            Status::Generating {
                since: Instant::now(),
            },
            Status::Ready,
        ] {
            app.set_status(status);
            assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
            assert_eq!(vibe_rs::terminal_notifier::title(&app), "? Vibe");
        }
        app.terminal_notifier
            .remember_completion(ProgramState::Error);
        if permission {
            vibe_rs::approval::apply_event(
                &mut app,
                vibe_rs::approval::Event::Responded {
                    callback_id: "a".into(),
                },
            );
        } else {
            vibe_rs::question_app::dismiss(
                &mut app,
                &vibe_rs::question_app::QuestionSource::Callback(
                    vibe_rs::server::CallbackAddress {
                        callback_id: "q".into(),
                        ..Default::default()
                    },
                ),
            );
        }
        report(&mut app, ProgramState::Error, "");
    }
}
#[test]
fn shell_completion_waits_for_its_final_effect_in_either_event_order() {
    // _shell.py::shell_effect_state; reader.rs::NotifSink can park the update past the RPC.
    for (status, outcome) in [
        ("completed", ProgramState::Done),
        ("failed", ProgramState::Error),
        ("cancelled", ProgramState::Idle),
    ] {
        for rpc_first in [false, true] {
            let mut app = app();
            app.session.shell_operation_id = Some("shell-1".into());
            app.session.shell_started_at = Some(Instant::now());
            app.view.command_loading = true;
            report(&mut app, ProgramState::Working, "");
            if rpc_first {
                program_status::shell_completed(&mut app, "shell-1", false);
                assert!(app.session.shell_rpc_completed);
            }
            notify(
                &mut app,
                notification::HISTORY_ENTRY_ADDED,
                json!({"entry":{
                    "id":"shell-1", "type":"effect", "state":{"status":"running"}, "detail":{"kind":"shell"}
                }}),
            );
            assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
            assert!(app.session.shell_operation_id.is_some() && app.view.command_loading);
            notify(
                &mut app,
                notification::HISTORY_ENTRY_UPDATED,
                json!({"entryId":"shell-1", "patch":[
                    {"op":"replace", "path":"/state", "value":{"status":status,"error":{"message":"Shell first line\nDetails"}}}
                ]}),
            );
            if !rpc_first {
                assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
                program_status::shell_completed(&mut app, "shell-1", false);
            }
            report(
                &mut app,
                outcome,
                if outcome == ProgramState::Error {
                    "Shell first line"
                } else {
                    ""
                },
            );
            assert!(app.session.shell_operation_id.is_none());
            assert!(!app.session.shell_rpc_completed && !app.view.command_loading);
            program_status::shell_completed(&mut app, "shell-1", false);
            assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
        }
    }
}
#[test]
fn shell_rpc_failure_settles_without_history() {
    let mut app = app();
    app.session.shell_operation_id = Some("shell-1".into());
    program_status::shell_completed(&mut app, "unrelated", true);
    report(&mut app, ProgramState::Working, "");
    program_status::shell_completed_with_error(&mut app, "shell-1", Some("RPC failed\nDetails"));
    report(&mut app, ProgramState::Error, "RPC failed");
    assert!(app.session.shell_operation_id.is_none());
}
#[test]
fn shell_rpc_failure_after_execution_reports_error() {
    // _unified_harness_backend_adapter.py::_dispatch_shell_command injects after effect completion.
    let mut app = app();
    app.session.shell_operation_id = Some("shell-1".into());
    app.view.command_loading = true;
    notify(
        &mut app,
        notification::HISTORY_ENTRY_ADDED,
        json!({"entry":{
            "id":"shell-1", "type":"effect", "state":{"status":"completed"},
            "detail":{"kind":"shell"}
        }}),
    );
    report(&mut app, ProgramState::Working, "");
    program_status::shell_completed(&mut app, "shell-1", true);
    report(&mut app, ProgramState::Error, "Shell command failed");
    assert!(app.session.shell_operation_id.is_none() && !app.view.command_loading);
}
#[test]
fn session_handoff_abandons_the_old_shell_and_ignores_its_late_reply() {
    for rpc_completed in [false, true] {
        let mut app = app();
        app.session.shell_operation_id = Some("old-shell".into());
        app.session.shell_started_at = Some(Instant::now());
        app.session.shell_request_started = true;
        app.session.shell_rpc_completed = rpc_completed;
        app.session.shell_interrupt_requested = true;
        app.view.command_loading = true;
        report(&mut app, ProgramState::Working, "");
        app.set_session_id("new-session".into());
        assert!(app.session.shell_operation_id.is_none());
        assert!(app.session.shell_started_at.is_none());
        assert!(!app.session.shell_request_started && !app.session.shell_rpc_completed);
        assert!(!app.session.shell_interrupt_requested && !app.view.command_loading);
        report(&mut app, ProgramState::Idle, "");
        program_status::shell_completed(&mut app, "old-shell", false);
        assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
    }
    let mut app = app();
    app.view.command_loading = true;
    app.set_session_id("new-session".into());
    assert!(app.view.command_loading);
}
#[test]
fn crash_error_survives_dialog_dismissal_and_pending_work() {
    for shell in [false, true] {
        let mut app = app();
        if shell {
            app.session.shell_operation_id = Some("shell-1".into());
            app.session.shell_rpc_completed = true;
            app.view.command_loading = true;
        }
        vibe_rs::message_queue::sync(
            &mut app,
            &json!({"paused":false,"items":[{
                "id":"queued", "entries":[{"entryId":"prompt","role":"user","content":[]}]
            }]}),
        );
        vibe_rs::question_app::on_callback_call(
            &mut app,
            &json!({
                "callback":{"callbackId":"q","detail":{"kind":"user_input","request":{
                    "questions":[{"question":"Pick?","options":[]}]
                }}}
            }),
        );
        app.approval
            .enqueue(vibe_rs::server::ApprovalCallback {
                address: vibe_rs::server::CallbackAddress {
                    callback_id: "a".into(),
                    ..Default::default()
                },
                ..Default::default()
            })
            .unwrap();
        vibe_rs::approval::show_pending(&mut app);
        app.terminal_notifier.take_bell();
        assert!(vibe_rs::event_loop::notifications::surface_server_close(
            &mut app, true
        ));
        report(
            &mut app,
            ProgramState::Error,
            "the app server exited unexpectedly",
        );
        vibe_rs::approval::apply_event(
            &mut app,
            vibe_rs::approval::Event::Responded {
                callback_id: "a".into(),
            },
        );
        assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
        vibe_rs::question_app::dismiss(
            &mut app,
            &vibe_rs::question_app::QuestionSource::Callback(vibe_rs::server::CallbackAddress {
                callback_id: "q".into(),
                ..Default::default()
            }),
        );
        assert!(vibe_rs::terminal_notifier::take_program_report(&mut app).is_none());
        assert!(!app.terminal_notifier.take_bell());
        assert!(app.session.shell_operation_id.is_none() && !app.session.shell_rpc_completed);
        assert!(!app.view.command_loading);
    }
}
