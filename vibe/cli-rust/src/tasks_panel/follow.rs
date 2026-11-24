//! Reads of a task's output: the latest page, then followed while the view stays open.

use std::sync::Arc;
use std::time::Duration;

use base64::{engine::general_purpose::STANDARD, Engine};

use super::output::{line_start, KEEP_BYTES};
use super::Appended;
use crate::app::App;
use crate::commands::CommandEvent;
use crate::selection::RegionId;
use crate::server::{method, BackgroundProcessOutput, BackgroundProcessOutputParams, Client};

#[cfg(test)]
mod tests;

/// Bytes one read asks for, the most the server sends.
const PAGE_BYTES: u64 = 64_000;
/// How long the server holds a follow read open waiting for new output.
const WAIT_MS: u64 = 1_000;
/// Pause between follow reads that caught up, so a server that answers at once is never hammered.
const PACE: Duration = Duration::from_millis(250);

/// Which page a read asks for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Read {
    /// The last page, in place of whatever the view held.
    Latest,
    /// What follows the cursor, after a pause once caught up.
    Next { paced: bool },
}

/// One read's answer for the view opened at `generation`.
pub struct Page {
    generation: u64,
    from_end: bool,
    read: Option<BackgroundProcessOutput>,
}

/// The read after a page: the latest one when the cap would drop all that is still unread, else the next, paced once caught up.
pub(super) fn next_read(read: &BackgroundProcessOutput) -> Read {
    match read.bytes_available.saturating_sub(read.next_cursor) {
        behind if behind > KEEP_BYTES as u64 => Read::Latest,
        behind => Read::Next { paced: behind == 0 },
    }
}

/// Store a read's bytes in the view it was asked for, then follow the output while that view stays open.
pub fn apply(app: &mut App, client: &Arc<Client>, page: Page) {
    let shown = crate::rail::window(app) == Some(crate::rail::Item::Tasks);
    let Some(view) = app.tasks_panel.shown_mut() else {
        return;
    };
    let output = &mut view.output;
    if output.generation != page.generation {
        return;
    }
    let following = std::mem::replace(&mut output.loaded, true);
    let Some(read) = page.read else {
        // A failed follow read retries; only a failed first read has no output to follow.
        match following && shown {
            true => request(app, client, Read::Next { paced: true }),
            false => output.unavailable = true,
        }
        return;
    };
    if read.availability != "available" {
        output.unavailable = true;
        return;
    }
    let bytes = STANDARD.decode(&read.output_base64).unwrap_or_default();
    output.cursor = read.next_cursor;
    // Done once the process ended and every byte was read.
    let done = read.is_final && read.next_cursor >= read.bytes_available;
    let mut moved = Appended::default();
    let mut page_bytes = &bytes[..];
    if page.from_end {
        moved = view.restart();
        // A last page that starts mid-output shows from its first whole line.
        if read.next_cursor > bytes.len() as u64 {
            page_bytes = &bytes[line_start(&bytes).0..];
        }
    }
    if !page_bytes.is_empty() {
        let appended = view.append(page_bytes);
        moved.rows += appended.rows;
        moved.mid_line |= appended.mid_line;
    }
    trimmed(app, &moved);
    if shown && !done {
        request(app, client, next_read(&read));
    }
}

/// Keep the view and its selection on their text when rows went away above them.
fn trimmed(app: &mut App, appended: &Appended) {
    // A line cut at its start rewraps fewer rows: a selection on it no longer holds its text.
    if appended.mid_line {
        crate::selection::clear_region(app, RegionId::RailWindow);
    }
    if let crate::rail::Scroll::Row(row) = &mut app.rail.scroll {
        *row = row.saturating_sub(appended.rows);
    }
    let rows = i32::try_from(appended.rows).unwrap_or(i32::MAX);
    crate::selection::shift(app, RegionId::RailWindow, -rows);
}

/// Ask for `read`; a follow read is held open until new output comes.
pub(super) fn request(app: &mut App, client: &Arc<Client>, read: Read) {
    let (Some(session_id), Some(view)) = (app.session.session_id.clone(), app.tasks_panel.shown())
    else {
        return;
    };
    let (generation, first) = (view.output.generation, !view.output.loaded);
    let from_end = read == Read::Latest;
    let params = BackgroundProcessOutputParams {
        session_id,
        process_id: view.output.process_id.clone(),
        from_end,
        cursor: if from_end { 0 } else { view.output.cursor },
        wait_ms: if from_end { 0 } else { WAIT_MS },
        max_bytes: PAGE_BYTES,
    };
    let (client, tx) = (client.clone(), app.command_tx.clone());
    let task = async move {
        if read == (Read::Next { paced: true }) {
            crate::clock::sleep_real(PACE).await;
        }
        let value = serde_json::to_value(params).unwrap_or_default();
        let read = client
            .request(method::BACKGROUND_PROCESS_OUTPUT, value)
            .await
            .ok()
            .and_then(|value| serde_json::from_value(value).ok());
        let page = Page {
            generation,
            from_end,
            read,
        };
        crate::input::deliver(tx, CommandEvent::TaskOutput(Box::new(page))).await;
    };
    // The first page lands before a settled frame; following never holds the client busy.
    match first {
        true => drop(crate::work::spawn(task)),
        false => drop(crate::work::spawn_daemon(task)),
    }
}
