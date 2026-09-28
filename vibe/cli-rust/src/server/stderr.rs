//! Drains the app-server's stderr into the file log; the TUI owns the terminal.

use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};

/// Target of forwarded child output; the file sink never level-filters it.
pub const TARGET: &str = "app_server";

/// Longest stderr line kept; a runaway child must not flood the log file.
const MAX_LINE: usize = 4 * 1024;

/// Reads bounded byte chunks until EOF, logging one record per delimiter.
/// Bytes past `MAX_LINE` are discarded as they arrive, so an unterminated
/// record costs `MAX_LINE`, and invalid UTF-8 never ends the drain.
pub async fn drain<R: AsyncRead + Unpin>(stderr: R) {
    let mut reader = BufReader::new(stderr);
    let mut line: Vec<u8> = Vec::new();
    loop {
        let chunk = match reader.fill_buf().await {
            Ok([]) | Err(_) => break,
            Ok(chunk) => chunk,
        };
        let end = chunk.iter().position(|byte| *byte == b'\n');
        let piece = &chunk[..end.unwrap_or(chunk.len())];
        let room = MAX_LINE - line.len();
        let consumed = piece.len() + usize::from(end.is_some());
        line.extend_from_slice(&piece[..piece.len().min(room)]);
        reader.consume(consumed);
        if end.is_some() {
            emit(&line);
            line.clear();
        }
    }
    emit(&line);
}

/// A record filled to the cap was clipped, so it ends in an ellipsis.
fn emit(line: &[u8]) {
    let text = String::from_utf8_lossy(line);
    let text = text.trim_end();
    if text.trim_start().is_empty() {
        return;
    }
    let ellipsis = if line.len() == MAX_LINE { "..." } else { "" };
    tracing::warn!(target: TARGET, "{text}{ellipsis}");
}
