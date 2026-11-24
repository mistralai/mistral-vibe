//! The Sessions page's lease lock: a session locks only when another process
//! holds its lease. The attached session and this client's own app-server
//! never lock a row, and a locked row refuses to open.

use crossterm::event::KeyCode;
use serde_json::json;

use vibe_rs::app::Severity;
use vibe_rs::sessions_page::leases::{self, apply_snapshot};
use vibe_rs::sessions_page::{held, keys, lease_locked};

use crate::feedback_support::{notice, warnings};
use crate::support::{stub, with_page};

const BUSY: &str = "This session is open in another Vibe process. Close it there to continue here.";

#[test]
fn a_foreign_holder_locks_the_session() {
    let snapshot = json!({"held": [
        {"sessionId": "foreign", "leaseHolder": {"processId": 4242}},
        {"sessionId": "current", "leaseHolder": {"processId": 100}},
        {"sessionId": "free"},
        {"sessionId": "torn", "leaseHolder": {"processId": null}},
        {"sessionId": "released", "leaseHolder": null},
    ]});
    let locked = held(&snapshot, None, Some("current"));
    assert_eq!(locked, ["foreign".to_owned()].into_iter().collect());
}

#[test]
fn the_current_session_never_locks() {
    let holder = json!({"processId": 4242});
    assert!(lease_locked(Some(&holder), None, false));
    assert!(!lease_locked(Some(&holder), None, true));
    assert!(!lease_locked(None, None, false));
}

#[test]
fn a_lease_held_by_the_own_app_server_never_locks() {
    let holder = json!({"processId": 4242});
    assert!(!lease_locked(Some(&holder), Some(4242), false));
    assert!(!lease_locked(Some(&holder), Some(4242), true));
    assert!(lease_locked(Some(&holder), Some(7), false));
}

#[test]
fn the_own_app_server_pid_exempts_snapshot_locks() {
    let snapshot = json!({"held": [
        {"sessionId": "swept", "leaseHolder": {"processId": 100}},
        {"sessionId": "foreign", "leaseHolder": {"processId": 4242}},
    ]});
    let locked = held(&snapshot, Some(100), Some("current"));
    assert_eq!(locked, ["foreign".to_owned()].into_iter().collect());
}

#[test]
fn a_missing_snapshot_locks_nothing() {
    assert!(held(&json!({}), None, Some("current")).is_empty());
}

#[test]
fn lease_updates_keep_the_lock_set_warm() {
    let mut app = with_page();
    leases::on_updated(
        &mut app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": {"processId": 4242}}),
    );
    assert!(app.sessions_page.locked.contains("busy"));
    leases::on_updated(
        &mut app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": null}),
    );
    assert!(!app.sessions_page.locked.contains("busy"));
    leases::on_updated(
        &mut app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": {"processId": 4242}}),
    );
    leases::on_ended(&mut app);
    assert!(app.sessions_page.locked.is_empty());
}

#[test]
fn a_late_lease_snapshot_keeps_newer_notifications() {
    let mut app = with_page();
    leases::on_updated(
        &mut app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": null}),
    );
    leases::on_updated(
        &mut app,
        &stub(),
        &json!({"sessionId": "taken", "leaseHolder": {"processId": 7}}),
    );
    apply_snapshot(
        &mut app,
        ["busy".into(), "other".into()].into_iter().collect(),
    );
    assert!(!app.sessions_page.locked.contains("busy"));
    assert!(app.sessions_page.locked.contains("taken"));
    assert!(app.sessions_page.locked.contains("other"));
}

#[test]
fn a_late_lease_snapshot_does_not_restore_locks_after_the_watch_ends() {
    let mut app = with_page();
    leases::on_updated(
        &mut app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": {"processId": 4242}}),
    );
    leases::on_ended(&mut app);
    apply_snapshot(
        &mut app,
        ["busy".into(), "other".into()].into_iter().collect(),
    );
    assert!(app.sessions_page.locked.is_empty());
}

#[test]
fn a_failed_lease_subscribe_does_not_clear_locks_already_collected() {
    let mut app = with_page();
    leases::on_updated(
        &mut app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": {"processId": 4242}}),
    );
    apply_snapshot(&mut app, Default::default());
    assert!(app.sessions_page.locked.contains("busy"));
}

#[test]
fn a_lease_snapshot_still_seeds_locks_before_any_notification() {
    let mut app = with_page();
    apply_snapshot(
        &mut app,
        ["busy".into(), "other".into()].into_iter().collect(),
    );
    assert!(app.sessions_page.locked.contains("busy"));
    assert!(app.sessions_page.locked.contains("other"));
    leases::on_updated(
        &mut app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": null}),
    );
    assert!(!app.sessions_page.locked.contains("busy"));
    assert!(app.sessions_page.locked.contains("other"));
}

#[tokio::test]
async fn enter_on_a_locked_row_warns_with_the_busy_refusal_instead_of_resuming() {
    let mut app = with_page();
    let _channels = vibe_rs::channels::connect(&mut app);
    leases::on_updated(
        &mut app,
        &stub(),
        &json!({"sessionId": "busy", "leaseHolder": {"processId": 4242}}),
    );
    keys::handle_key(&mut app, &stub(), KeyCode::Enter.into());
    assert!(app.sessions_page.busy.is_none());
    assert!(app.sessions_page.open);
    assert_eq!(notice(&app), Some((Severity::Warning, BUSY)));
    assert!(warnings(&app).is_empty());
}

#[tokio::test]
async fn enter_on_an_unlocked_row_still_resumes() {
    let mut app = with_page();
    app.set_status(vibe_rs::app::Status::Ready);
    let _channels = vibe_rs::channels::connect(&mut app);
    keys::handle_key(&mut app, &stub(), KeyCode::Enter.into());
    assert_eq!(app.sessions_page.busy.as_deref(), Some("busy"));
}
