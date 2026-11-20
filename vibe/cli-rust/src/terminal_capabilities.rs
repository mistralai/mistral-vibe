//! What the terminal answered to the startup probe, kept for `vibe.startup`.

use std::sync::OnceLock;

/// XTVERSION (`CSI > q`); the startup probe sends it. No keyboard query: startup must not wait on one (VIBE-4768).
pub const QUERY: &[u8] = b"\x1b[>q";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Capabilities {
    /// `answered` (DA1 arrived), `unanswered`, `not_queried` (no tty or pending input) or `skipped` (multiplexer, Windows).
    pub status: &'static str,
    /// The XTVERSION text, such as `ghostty 1.1.3`.
    pub version: Option<String>,
    pub mouse_pixel_reports: Option<bool>,
}

impl Default for Capabilities {
    fn default() -> Self {
        Self {
            status: "skipped",
            version: None,
            mouse_pixel_reports: None,
        }
    }
}

static PROBED: OnceLock<Capabilities> = OnceLock::new();

/// Keep the probe's reply; `None` when the terminal could not be queried.
pub fn record(response: Option<&[u8]>) {
    let _ = PROBED.set(from_response(response));
}

pub fn from_response(response: Option<&[u8]>) -> Capabilities {
    response.map_or_else(
        || Capabilities {
            status: "not_queried",
            ..Capabilities::default()
        },
        read,
    )
}

/// The recorded capabilities, `skipped` when the probe never ran.
pub fn probed() -> Capabilities {
    PROBED.get().cloned().unwrap_or_default()
}

pub fn read(response: &[u8]) -> Capabilities {
    let answered = crate::terminal_probe::is_answered(response);
    Capabilities {
        status: if answered { "answered" } else { "unanswered" },
        version: xtversion(response),
        mouse_pixel_reports: Some(crate::mouse::pixels::supports(response)),
    }
}

/// The XTVERSION reply text (`DCS > | text ST`).
pub fn xtversion(response: &[u8]) -> Option<String> {
    let prefix = b"\x1bP>|";
    let start = response
        .windows(prefix.len())
        .position(|window| window == prefix)?
        + prefix.len();
    let rest = &response[start..];
    let end = rest.iter().position(|&byte| byte == 0x1b || byte == 0x07)?;
    let text = String::from_utf8_lossy(&rest[..end]).trim().to_owned();
    (!text.is_empty()).then_some(text)
}
