//! Bounded NDJSON framing: an oversized frame is skipped and fails only its request.

use serde_json::json;
use tokio::sync::mpsc;

use super::process::{Pending, RequestFailure};
use super::reader::next_frame;

/// Maximum accepted JSON-RPC frame size (bytes); a larger one is skipped, the transport stays up.
pub const MAX_FRAME: usize = 16 * 1024 * 1024;

/// Leading bytes kept from an oversized frame, enough to read its id.
const PREFIX_MAX: usize = 4096;

type Writer = mpsc::UnboundedSender<Option<String>>;

/// Stdout bytes not yet split into frames, plus the oversized frame being skipped.
#[derive(Default)]
pub struct Frames {
    tail: Vec<u8>,
    searched: usize,
    skipping: Option<Skipped>,
}

struct Skipped {
    prefix: Vec<u8>,
    len: usize,
}

/// An oversized frame that expects an answer, read from its first bytes.
#[derive(Debug, PartialEq, Eq)]
pub enum Dropped {
    /// A response to the client's request `id`, which fails with `FrameTooLarge`.
    Response(u64),
    /// A server request `id`, answered with a JSON-RPC error.
    Request(u64),
}

impl Frames {
    /// The complete frames `bytes` finishes; oversized ones are dropped instead.
    pub fn push(&mut self, mut bytes: &[u8], pending: &Pending, writer: &Writer) -> Vec<String> {
        if let Some(skipped) = self.skipping.as_mut() {
            let Some(end) = bytes.iter().position(|&byte| byte == b'\n') else {
                skipped.len += bytes.len();
                return Vec::new();
            };
            skipped.len += end;
            bytes = &bytes[end + 1..];
            if let Some(skipped) = self.skipping.take() {
                skipped.reject(pending, writer);
            }
        }
        self.tail.extend_from_slice(bytes);
        let mut frames = Vec::new();
        while let Some(frame) = next_frame(&mut self.tail, &mut self.searched) {
            if frame.len() > MAX_FRAME {
                Skipped::new(frame.as_bytes(), frame.len()).reject(pending, writer);
            } else {
                frames.push(frame);
            }
        }
        if self.tail.len() > MAX_FRAME {
            self.skipping = Some(Skipped::new(&self.tail, self.tail.len()));
            self.tail = Vec::new();
            self.searched = 0;
        }
        frames
    }

    /// At EOF: the final unterminated frame, if any.
    pub fn finish(&mut self, pending: &Pending, writer: &Writer) -> Option<String> {
        if let Some(skipped) = self.skipping.take() {
            skipped.reject(pending, writer);
        }
        if self.tail.is_empty() {
            return None;
        }
        let frame = String::from_utf8_lossy(&self.tail)
            .trim_end_matches('\r')
            .to_string();
        self.tail.clear();
        self.searched = 0;
        Some(frame)
    }
}

impl Skipped {
    fn new(bytes: &[u8], len: usize) -> Self {
        let prefix = bytes[..bytes.len().min(PREFIX_MAX)].to_vec();
        Self { prefix, len }
    }

    fn reject(self, pending: &Pending, writer: &Writer) {
        let dropped = dropped(&self.prefix);
        tracing::error!(len = self.len, ?dropped, "frame exceeds MAX_FRAME; dropped");
        match dropped {
            Some(Dropped::Response(id)) => {
                let waiter = pending
                    .lock()
                    .unwrap_or_else(|err| err.into_inner())
                    .remove(&id);
                if let Some(tx) = waiter {
                    let _ = tx.send(Err(RequestFailure::FrameTooLarge(self.len)));
                }
            }
            Some(Dropped::Request(id)) => {
                let message = format!("Request too large for the client ({} bytes)", self.len);
                let error = json!({"code": "invalid_request", "message": message});
                let reply = json!({"jsonrpc": "2.0", "id": id, "error": error});
                let _ = writer.send(Some(reply.to_string()));
            }
            None => {}
        }
    }
}

/// `"id":N` followed by `"result"`/`"error"` (a response) or `"method"` (a server request).
pub fn dropped(prefix: &[u8]) -> Option<Dropped> {
    let text = String::from_utf8_lossy(prefix);
    let start = text.find("\"id\":")? + "\"id\":".len();
    let rest = text[start..].trim_start();
    let digits = rest.len() - rest.trim_start_matches(|c: char| c.is_ascii_digit()).len();
    let id = rest[..digits].parse().ok()?;
    let next = rest[digits..].trim_start().strip_prefix(',')?.trim_start();
    if next.starts_with("\"result\"") || next.starts_with("\"error\"") {
        return Some(Dropped::Response(id));
    }
    next.starts_with("\"method\"")
        .then_some(Dropped::Request(id))
}
