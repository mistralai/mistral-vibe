//! An oversized frame fails only the request it answers; framing resumes after it.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use tokio::sync::{mpsc, oneshot};
use vibe_rs::server::frames::{dropped, Dropped, Frames, MAX_FRAME};
use vibe_rs::server::{Pending, RequestFailure};

type Response = oneshot::Receiver<Result<serde_json::Value, RequestFailure>>;
type Written = mpsc::UnboundedReceiver<Option<String>>;

struct Peer {
    pending: Pending,
    response: Response,
    writer: mpsc::UnboundedSender<Option<String>>,
    written: Written,
}

fn peer_waiting_on(id: u64) -> Peer {
    let (tx, response) = oneshot::channel();
    let (writer, written) = mpsc::unbounded_channel();
    let pending: Pending = Arc::new(Mutex::new(HashMap::from([(id, tx)])));
    Peer {
        pending,
        response,
        writer,
        written,
    }
}

/// Feeds `bytes` in stdout-sized reads, collecting the frames they complete.
fn push_in_reads(frames: &mut Frames, bytes: &[u8], peer: &Peer) -> Vec<String> {
    bytes
        .chunks(16384)
        .flat_map(|chunk| frames.push(chunk, &peer.pending, &peer.writer))
        .collect()
}

fn oversized(head: &str) -> Vec<u8> {
    let mut frame = format!(r#"{head}{{"text":""#).into_bytes();
    frame.resize(frame.len() + MAX_FRAME, b'x');
    frame.extend_from_slice(b"\"}}\n");
    frame
}

#[test]
fn oversized_response_fails_its_request_and_later_frames_still_route() {
    let mut peer = peer_waiting_on(7);
    let mut frames = Frames::default();
    let mut bytes = oversized(r#"{"jsonrpc":"2.0","id":7,"result":"#);
    bytes.extend_from_slice(b"{\"jsonrpc\":\"2.0\",\"id\":8,\"result\":{}}\n");

    let routed = push_in_reads(&mut frames, &bytes, &peer);

    assert_eq!(routed, [r#"{"jsonrpc":"2.0","id":8,"result":{}}"#]);
    assert!(matches!(
        peer.response.try_recv(),
        Ok(Err(RequestFailure::FrameTooLarge(len))) if len > MAX_FRAME
    ));
    assert!(peer.pending.lock().unwrap().is_empty());
    assert!(peer.written.try_recv().is_err());
}

#[test]
fn oversized_frame_completed_in_one_read_is_dropped() {
    let mut peer = peer_waiting_on(3);
    let mut frames = Frames::default();

    let routed = frames.push(
        &oversized(r#"{"jsonrpc":"2.0","id":3,"result":"#),
        &peer.pending,
        &peer.writer,
    );

    assert!(routed.is_empty());
    assert!(matches!(
        peer.response.try_recv(),
        Ok(Err(RequestFailure::FrameTooLarge(_)))
    ));
}

#[test]
fn oversized_server_request_is_answered_with_an_error() {
    let mut peer = peer_waiting_on(1);
    let mut frames = Frames::default();
    let bytes = oversized(r#"{"jsonrpc":"2.0","id":1,"method":"callback/call","params":"#);

    let routed = push_in_reads(&mut frames, &bytes, &peer);

    assert!(routed.is_empty());
    let reply: serde_json::Value =
        serde_json::from_str(&peer.written.try_recv().unwrap().unwrap()).unwrap();
    assert_eq!(reply["id"], 1);
    assert_eq!(reply["error"]["code"], "invalid_request");
    assert!(peer.response.try_recv().is_err());
}

#[test]
fn oversized_notification_is_dropped_without_failing_requests() {
    let mut peer = peer_waiting_on(1);
    let mut frames = Frames::default();
    let mut bytes =
        oversized(r#"{"jsonrpc":"2.0","method":"history/entryAdded","params":{"id":1,"entry":"#);
    bytes.extend_from_slice(b"{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\n");

    let routed = push_in_reads(&mut frames, &bytes, &peer);

    assert_eq!(routed, [r#"{"jsonrpc":"2.0","id":1,"result":{}}"#]);
    assert!(peer.response.try_recv().is_err());
    assert!(peer.written.try_recv().is_err());
}

#[test]
fn dropped_reads_only_a_leading_id() {
    assert_eq!(
        dropped(br#"{"jsonrpc":"2.0","id":42,"result":{"#),
        Some(Dropped::Response(42))
    );
    assert_eq!(
        dropped(br#"{"jsonrpc":"2.0","id": 5, "error":{"#),
        Some(Dropped::Response(5))
    );
    assert_eq!(
        dropped(br#"{"jsonrpc":"2.0","id":9,"method":"callback/call""#),
        Some(Dropped::Request(9))
    );
    assert_eq!(
        dropped(br#"{"jsonrpc":"2.0","method":"x","params":{"#),
        None
    );
}
