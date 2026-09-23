//! Error frames must parse whatever shape the peer's `code` takes, or the
//! request they answer never settles.

use vibe_rs::server::{Incoming, RequestFailure};

const STRING_CODE: &str = r#"{"jsonrpc":"2.0","id":7,"error":{"code":"conflict","message":"Session is already open: abc","data":null}}"#;
const NUMERIC_CODE: &str =
    r#"{"jsonrpc":"2.0","id":8,"error":{"code":-32601,"message":"Method not found"}}"#;
const NAMED_INVALID_PARAMS: &str =
    r#"{"jsonrpc":"2.0","id":9,"error":{"code":"invalid_params","message":"Invalid image"}}"#;
const NUMERIC_INVALID_PARAMS: &str =
    r#"{"jsonrpc":"2.0","id":10,"error":{"code":-32602,"message":"Invalid image"}}"#;
// The Unified Harness maps `turn_queue_item_not_found` to this protocol code.
const NAMED_NOT_FOUND: &str =
    r#"{"jsonrpc":"2.0","id":11,"error":{"code":"not_found","message":"Queue item not found"}}"#;
const MISLEADING_NOT_FOUND: &str = r#"{"jsonrpc":"2.0","id":12,"error":{"code":"conflict","message":"[not_found] is only display text"}}"#;

/// The app-server answers `session/resume` on a held session with a string code;
/// dropping that frame leaves the resume pending forever.
#[test]
fn error_frame_with_a_string_code_parses() {
    let incoming: Incoming = serde_json::from_str(STRING_CODE).expect("frame parses");
    assert_eq!(incoming.id, Some(7));
    let error = incoming.error.expect("frame carries an error");
    assert_eq!(error.message, "Session is already open: abc");
}

#[test]
fn error_codes_render_unquoted() {
    let string: Incoming = serde_json::from_str(STRING_CODE).expect("frame parses");
    let numeric: Incoming = serde_json::from_str(NUMERIC_CODE).expect("frame parses");
    assert_eq!(string.error.expect("error").code(), "conflict");
    assert_eq!(numeric.error.expect("error").code(), "-32601");
}

#[test]
fn invalid_params_is_recognized_in_named_and_numeric_forms() {
    for frame in [NAMED_INVALID_PARAMS, NUMERIC_INVALID_PARAMS] {
        let incoming: Incoming = serde_json::from_str(frame).expect("frame parses");
        let failure = RequestFailure::Rpc(incoming.error.expect("error"));
        assert!(failure.is_invalid_params());
    }

    let method_not_found: Incoming = serde_json::from_str(NUMERIC_CODE).expect("frame parses");
    let failure = RequestFailure::Rpc(method_not_found.error.expect("error"));
    assert!(!failure.is_invalid_params());
}

#[test]
fn structured_rpc_code_survives_request_context() {
    let incoming: Incoming = serde_json::from_str(NAMED_INVALID_PARAMS).expect("frame parses");
    let failure = RequestFailure::Rpc(incoming.error.expect("error"));
    let error = anyhow::Error::new(failure).context("workspace/prompt/prepare failed");

    assert!(error
        .downcast_ref::<RequestFailure>()
        .is_some_and(RequestFailure::is_invalid_params));
}

#[test]
fn not_found_code_survives_request_context() {
    let incoming: Incoming = serde_json::from_str(NAMED_NOT_FOUND).expect("frame parses");
    let failure = RequestFailure::Rpc(incoming.error.expect("error"));
    let error = anyhow::Error::new(failure).context("session/turn/queue/replace failed");

    assert!(error
        .downcast_ref::<RequestFailure>()
        .is_some_and(RequestFailure::is_not_found));

    let misleading: Incoming = serde_json::from_str(MISLEADING_NOT_FOUND).expect("frame parses");
    assert!(!RequestFailure::Rpc(misleading.error.expect("error")).is_not_found());
}
