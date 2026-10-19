//! A session-busy conflict is recognized by protocol code + harnessCode, and
//! the resume notice maps it to the desktop app's busy-session wording.

use vibe_rs::server::{request_error, Incoming, RequestFailure, RpcError};

const BUSY: &str = r#"{"jsonrpc":"2.0","id":1,"error":{"code":"conflict","message":"Session is already open: abc","data":{"harnessCode":"session_busy","details":{"session_id":"abc"}}}}"#;
const OTHER_CONFLICT: &str = r#"{"jsonrpc":"2.0","id":2,"error":{"code":"conflict","message":"Some other conflict","data":{"harnessCode":"something_else"}}}"#;
const BUSY_WITH_OTHER_CODE: &str = r#"{"jsonrpc":"2.0","id":3,"error":{"code":"internal_error","message":"Session is already open: abc","data":{"harnessCode":"session_busy"}}}"#;
const NUMERIC_CODE: &str =
    r#"{"jsonrpc":"2.0","id":4,"error":{"code":-32601,"message":"Method not found"}}"#;
const NEWER_STORE: &str = r#"{"jsonrpc":"2.0","id":5,"error":{"code":"internal_error","message":"This session was written by a newer store format (1.3) and needs a newer reader to open.","data":{"harnessCode":"store_requires_newer_reader"}}}"#;

fn rpc(frame: &str) -> RpcError {
    let incoming: Incoming = serde_json::from_str(frame).expect("frame parses");
    incoming.error.expect("frame carries an error")
}

/// `Client::request` wraps failures as `"{method} failed"` context over the
/// `RequestFailure`; the notice helpers must see through it.
fn request_failure(rpc: RpcError) -> anyhow::Error {
    anyhow::Error::new(RequestFailure::Rpc(rpc)).context("session/resume failed")
}

#[test]
fn busy_conflict_is_recognized() {
    let error = rpc(BUSY);
    assert_eq!(error.harness_code(), Some("session_busy"));
    assert!(error.is_session_busy());
}

#[test]
fn other_errors_are_not_busy() {
    assert!(!rpc(OTHER_CONFLICT).is_session_busy());
    assert!(!rpc(NUMERIC_CODE).is_session_busy());
    // Stricter than Python's harness-code-only check on purpose: the protocol
    // code is part of the classification, and the server always maps a busy
    // lease to `conflict`.
    assert!(!rpc(BUSY_WITH_OTHER_CODE).is_session_busy());
}

#[test]
fn busy_conflict_shows_the_open_elsewhere_message() {
    let message = request_error::resume_failure_message(&request_failure(rpc(BUSY)));
    assert_eq!(
        message,
        "This session is open in another Vibe process. Close it there to continue here."
    );
    assert_eq!(message, request_error::SESSION_BUSY_MESSAGE);
}

/// The startup attach path adds `state_of`'s method context over
/// `Client::request`'s; the busy mapping must see through both layers.
#[test]
fn busy_conflict_survives_the_attach_context_chain() {
    let error = request_failure(rpc(BUSY)).context("session/resume");
    let message = request_error::resume_failure_message(&error);
    assert_eq!(message, request_error::SESSION_BUSY_MESSAGE);
}

#[test]
fn newer_store_shows_the_upgrade_message() {
    let message = request_error::resume_failure_message(&request_failure(rpc(NEWER_STORE)));
    assert_eq!(message, request_error::STORE_REQUIRES_NEWER_READER_MESSAGE);
}

#[test]
fn other_rpc_errors_show_the_prefixed_server_message() {
    let message = request_error::resume_failure_message(&request_failure(rpc(OTHER_CONFLICT)));
    assert_eq!(message, "Failed to resume session: Some other conflict");
}

#[test]
fn transport_errors_show_the_prefixed_error_chain() {
    let error =
        anyhow::Error::new(RequestFailure::AppServerClosed).context("session/resume failed");
    let message = request_error::resume_failure_message(&error);
    assert_eq!(
        message,
        "Failed to resume session: session/resume failed: app-server closed"
    );
}
