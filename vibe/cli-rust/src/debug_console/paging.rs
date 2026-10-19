//! The debug console's paging and polling: tasks off the main thread, answers
//! folded into the state on it.

use std::sync::atomic::Ordering;
use std::sync::Arc;

use serde_json::json;

use super::{
    DebugConsole, Event, LogEntry, LogPage, MAX_ENTRIES, MAX_VISUAL_LINES, PAGE_SIZE,
    POLL_INTERVAL, POLL_LIMIT,
};
use crate::app::App;
use crate::server::{method, Client};
use crate::utils::scroll::{scroll_view, ScrollView};
use crate::utils::text::wrap_hard;

/// Total wrapped visual lines across all entries.
pub fn total_visual(console: &DebugConsole) -> usize {
    console.wrap_counts.iter().sum()
}

/// The viewport over the log body: clamped scroll and the top line to render.
pub fn view(console: &DebugConsole) -> ScrollView {
    scroll_view(
        total_visual(console).min(u16::MAX as usize) as u16,
        console.body_height,
        console.scroll,
    )
}

/// Whether the viewport sits at the oldest loaded line.
pub fn at_top(console: &DebugConsole) -> bool {
    view(console).position == 0
}

/// Mount or remove the console; a no-op
/// before the session exists.
pub fn toggle(app: &mut App, client: &Arc<Client>) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let console = &mut app.debug_console;
    console.generation.fetch_add(1, Ordering::Relaxed);
    console.open = !console.open;
    reset(console);
    if console.open {
        // The poll task reads the session id from this watch on every tick, so
        // a session switch under an open console keeps the logs flowing.
        let (session_tx, session_rx) = tokio::sync::watch::channel(session_id);
        console.session_tx = Some(session_tx);
        start_polling(app, client, session_rx);
        load_page(app, client);
    }
}

/// Fresh widget state on every open. The
/// in-flight flag survives a toggle: a request spawned before the close is
/// still outstanding, and its answer releases the flag and re-drives paging.
fn reset(console: &mut DebugConsole) {
    clear_session(console);
    console.body_height = 0;
    console.wrap_width = 0;
    console.body_area = ratatui::layout::Rect::default();
    console.session_tx = None;
    console.visible = false;
}

/// Fresh log state for a session switch: the old session's entries, dedupe
/// ids and paging offset all belong to the previous log. The panel's `open`
/// state, poll task and session watch survive; the next poll repopulates the
/// console from the new session.
pub fn clear_session(console: &mut DebugConsole) {
    console.entries.clear();
    console.wrap_counts.clear();
    console.seen.clear();
    // The repopulating poll only advances an established cursor
    // (`apply_polled`) and `maybe_load_previous` refuses a `None` one, so
    // start at 0 (nothing to skip): the next poll turns it into the count of
    // entries it delivered, and older history stays reachable without a
    // first page (which a filled viewport never triggers).
    console.cursor = Some(0);
    console.has_more = true;
    console.scroll = 0;
    console.hitmap.clear();
}

/// One older page of the log, prepended on arrival; at most one request in
/// flight, serialized against polls.
pub fn load_page(app: &mut App, client: &Arc<Client>) {
    let console = &mut app.debug_console;
    if !console.open || console.page_in_flight.swap(true, Ordering::Relaxed) {
        return;
    }
    let Some(session_id) = app.session.session_id.clone() else {
        console.page_in_flight.store(false, Ordering::Relaxed);
        return;
    };
    let Some(tx) = console.tx.clone() else {
        console.page_in_flight.store(false, Ordering::Relaxed);
        return;
    };
    let offset = console.cursor.unwrap_or(0);
    let generation = console.generation.load(Ordering::Relaxed);
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let params = json!({"sessionId": session_id.clone(), "limit": PAGE_SIZE, "offset": offset});
        let page = client
            .request(method::DIAGNOSTICS_LOGS_READ, params)
            .await
            .inspect_err(|err| tracing::warn!(%err, "debug console page failed"))
            .ok()
            .and_then(|value| super::parse_page(&value));
        crate::input::deliver(
            Some(tx),
            Event::Page {
                generation,
                session: session_id,
                requested: offset,
                page,
            },
            &pending,
        )
        .await;
    });
}

/// The 0.5 s newest-entries poll. The task exits
/// when a toggle bumps the generation past its own: the channel's receiver
/// lives for the whole process, so a send never fails on close.
fn start_polling(
    app: &mut App,
    client: &Arc<Client>,
    mut session_rx: tokio::sync::watch::Receiver<String>,
) {
    let console = &app.debug_console;
    let Some(tx) = console.tx.clone() else {
        return;
    };
    let generation = console.generation.clone();
    let in_flight = console.page_in_flight.clone();
    let observed = generation.load(Ordering::Relaxed);
    let client = client.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(POLL_INTERVAL);
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        interval.tick().await; // the first tick is immediate; polls start one interval in
        loop {
            interval.tick().await;
            if generation.load(Ordering::Relaxed) != observed {
                return;
            }
            // A page load holds the flag for its whole request; skip this tick.
            if in_flight
                .compare_exchange(false, true, Ordering::Relaxed, Ordering::Relaxed)
                .is_err()
            {
                continue;
            }
            // The current session id, read fresh each tick: `/clear`,
            // `/compact`, `/rewind` and `/resume` switch it under an open
            // console.
            let session_id = session_rx.borrow_and_update().clone();
            let params = json!({"sessionId": session_id, "limit": POLL_LIMIT});
            let entries = client
                .request(method::DIAGNOSTICS_LOGS_READ, params)
                .await
                .inspect_err(|err| tracing::warn!(%err, "debug console poll failed"))
                .ok()
                .and_then(|value| super::parse_entries(&value))
                .unwrap_or_default();
            in_flight.store(false, Ordering::Relaxed);
            if tx.send(Event::Polled(observed, entries)).await.is_err() {
                return;
            }
        }
    });
}

/// Apply a page or poll answer on the main thread.
pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    match event {
        Event::Page {
            generation,
            session,
            requested,
            page,
        } => {
            // The request finished whatever happens to its payload.
            app.debug_console
                .page_in_flight
                .store(false, Ordering::Relaxed);
            let current = app.debug_console.generation.load(Ordering::Relaxed);
            if !app.debug_console.open
                || generation != current
                || app.session.session_id.as_deref() != Some(session.as_str())
            {
                // Closed, reopened mid-request, or the session switched under
                // an open console: drop the payload, but an open console still
                // needs its page for the current session.
                if app.debug_console.open {
                    load_page(app, client);
                }
                return;
            }
            let Some(page) = page else {
                return;
            };
            let keep_filling = apply_page(&mut app.debug_console, page, requested);
            // No snap to the bottom: the initial fill is bottom-pinned on its
            // own (the offset stays 0), and a page landing while the user
            // scrolled up keeps their viewport.
            if keep_filling {
                load_page(app, client);
            }
        }
        Event::Polled(generation, entries) => {
            let current = app.debug_console.generation.load(Ordering::Relaxed);
            if !app.debug_console.open || generation != current {
                // Closed or reopened mid-poll: drop the payload, but a
                // reopened console still needs its first page (the Page arm
                // re-drives paging the same way).
                if app.debug_console.open {
                    load_page(app, client);
                }
                return;
            }
            apply_polled(&mut app.debug_console, &entries);
            // A poll can hold the in-flight flag when a page answer releases
            // it, so the fill loop needs a nudge here as well as in the page arm.
            if app.debug_console.has_more
                && app.debug_console.entries.len() < MAX_ENTRIES
                && !is_filled(&app.debug_console)
                && !app.debug_console.page_in_flight.load(Ordering::Relaxed)
            {
                load_page(app, client);
            }
        }
    }
}

/// Whether the loaded content overflows the viewport. A panel that has
/// never drawn has no viewport height yet, so it counts as not filled: the
/// fill loop continues until a draw publishes a real height.
fn is_filled(console: &DebugConsole) -> bool {
    console.body_height != 0 && total_visual(console) > console.body_height as usize
}

/// Fold an older page into the state (the prepend keeps the viewport anchored).
/// Returns whether the fill-viewport loop should request the next page.
/// `requested` is the offset the page was fetched from.
pub fn apply_page(console: &mut DebugConsole, page: LogPage, requested: u64) -> bool {
    // Pages keep loading while the content fits the viewport. The
    // progress guard ends the loop when the server stops advancing the cursor
    // past the requested offset (a canned server repeating one page would
    // otherwise never stop); a queued poll may advance the cursor while the
    // page is in flight, so the request's own offset is the baseline.
    let progress = page.cursor.is_some_and(|cursor| cursor != requested);
    // A poll may have advanced the live offset while the page was in flight
    // (a session switch refetches from 0); the page's own cursor must not
    // rewind past it, or Home/Up walks already-loaded rows again.
    // The entry bound ends the loop: at `MAX_ENTRIES` trim drops what a page
    // prepends, so a console that never fills (a hidden panel) would refetch
    // the same page forever.
    console.cursor = page
        .cursor
        .map(|cursor| cursor.max(console.cursor.unwrap_or(0)));
    console.has_more = page.has_more;
    prepend_entries(console, page.entries);
    trim(console);
    console.has_more && progress && !is_filled(console) && console.entries.len() < MAX_ENTRIES
}

/// Fold the poll's new entries in, keeping a scrolled-up viewport anchored.
/// Returns the visual lines appended.
pub fn apply_polled(console: &mut DebugConsole, entries: &[LogEntry]) -> usize {
    let before = console.entries.len();
    let added_visual = append_entries(console, entries);
    if let Some(cursor) = console.cursor.as_mut() {
        *cursor += (console.entries.len() - before) as u64;
    }
    // Keep the top line while appending below it; a bottom-pinned view follows.
    if added_visual > 0 && console.scroll > 0 {
        console.scroll = console
            .scroll
            .saturating_add(added_visual.min(u16::MAX as usize) as u16);
    }
    trim(console);
    added_visual
}

/// ADR 0016 bound: past `MAX_ENTRIES` entries or `MAX_VISUAL_LINES` wrapped
/// lines the oldest entries are dropped. Their `seen` ids are evicted too,
/// so a later page that re-fetches the dropped range (the cursor stepped
/// back with them) is accepted and history stays continuous below the bound.
/// The scroll offset stays: dropping from the top shifts the content up, and
/// the unchanged offset from the bottom tracks it, so a scrolled-up viewport
/// keeps its content.
fn trim(console: &mut DebugConsole) {
    let mut excess = console.entries.len().saturating_sub(MAX_ENTRIES);
    let total = total_visual(console);
    if total > MAX_VISUAL_LINES {
        let mut dropped = 0;
        let mut lines = 0;
        for &count in &console.wrap_counts {
            if total - lines <= MAX_VISUAL_LINES {
                break;
            }
            lines += count;
            dropped += 1;
        }
        excess = excess.max(dropped);
    }
    if excess == 0 {
        return;
    }
    console.wrap_counts.drain(..excess).for_each(drop);
    for entry in console.entries.drain(..excess) {
        console.seen.remove(&entry.id);
    }
    if let Some(cursor) = console.cursor.as_mut() {
        *cursor = cursor.saturating_sub(excess as u64);
    }
}

/// Prepend an older page (newest-first on the wire) after the dedupe filter.
/// Returns how many entries were new.
fn prepend_entries(console: &mut DebugConsole, entries: Vec<LogEntry>) -> usize {
    let fresh: Vec<LogEntry> = entries
        .into_iter()
        .rev()
        .filter(|entry| !console.seen.contains(&entry.id))
        .collect();
    let counts: Vec<usize> = fresh
        .iter()
        .map(|entry| count_entry(console, entry))
        .collect();
    let added = fresh.len();
    for entry in &fresh {
        console.seen.insert(entry.id.clone());
    }
    console.entries.splice(0..0, fresh);
    console.wrap_counts.splice(0..0, counts);
    added
}

/// Append the poll's entries (newest-first on the wire) after the dedupe
/// filter. Returns the visual lines added.
fn append_entries(console: &mut DebugConsole, entries: &[LogEntry]) -> usize {
    let mut added_visual = 0;
    for entry in entries.iter().rev() {
        if !console.seen.insert(entry.id.clone()) {
            continue;
        }
        let count = count_entry(console, entry);
        added_visual += count;
        console.wrap_counts.push(count);
        console.entries.push(entry.clone());
    }
    added_visual
}

/// Visual lines one entry takes at the console's current wrap width.
fn count_entry(console: &DebugConsole, entry: &LogEntry) -> usize {
    wrap_hard(&entry.plain_line(), console.wrap_width as usize)
        .len()
        .max(1)
}
