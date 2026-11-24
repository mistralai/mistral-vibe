//! Wire types for the session's background processes (`session/backgroundProcess/*`).

use serde::{Deserialize, Serialize};

/// `PublicBackgroundProcess`: a process the agent started in the background.
#[derive(Debug, Clone, Deserialize)]
pub struct PublicBackgroundProcess {
    pub status: String,
    #[serde(rename = "processId")]
    pub process_id: String,
    #[serde(default)]
    pub command: String,
}

/// Stop one background process; the next session snapshot reports it stopped.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackgroundProcessStopParams {
    pub session_id: String,
    pub process_id: String,
}

/// Read a page of one background process's output, from its end or from `cursor`.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackgroundProcessOutputParams {
    pub session_id: String,
    pub process_id: String,
    pub from_end: bool,
    pub cursor: u64,
    pub wait_ms: u64,
    pub max_bytes: u64,
}

/// A page of output; an `unavailable` answer carries none of the byte fields.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BackgroundProcessOutput {
    pub availability: String,
    #[serde(default)]
    pub output_base64: String,
    #[serde(default)]
    pub next_cursor: u64,
    #[serde(default)]
    pub bytes_available: u64,
    #[serde(default)]
    pub is_final: bool,
}
