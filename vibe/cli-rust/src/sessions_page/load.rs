//! Loading the page: every `session/list` page in turn, and the local projects.

use std::sync::Arc;

use serde_json::{json, Value};

use super::row::{self, Session};
use super::{Event, MAX_SESSIONS};
use crate::app::App;
use crate::server::{method, Client};

/// The largest page `session/list` serves.
const PAGE_LIMIT: usize = 500;

/// Read one page; the next one is asked only once this one landed, so no
/// other `session/list` interleaves with the walk the server caches.
fn list(app: &App, client: &Arc<Client>, cursor: Option<String>) {
    if app.events.is_none() {
        return;
    }
    let generation = app.sessions_page.generation;
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let mut params = json!({ "limit": PAGE_LIMIT, "includeArchived": true });
        if let Some(cursor) = cursor {
            params["cursor"] = Value::String(cursor);
        }
        match client.request(method::SESSION_LIST, params).await {
            Ok(value) => Event::Page {
                generation,
                sessions: row::sessions(&value),
                next: value["nextCursor"].as_str().map(str::to_owned),
            },
            Err(error) => Event::Failed {
                generation,
                message: format!("Failed to list sessions: {error}"),
            },
        }
    });
}

fn projects(app: &App, client: &Arc<Client>) {
    if app.events.is_none() {
        return;
    }
    let generation = app.sessions_page.generation;
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let projects = client
            .request(method::PROJECTS_LIST, json!({}))
            .await
            .map(|value| row::projects(&value))
            .unwrap_or_default();
        Event::Projects {
            generation,
            projects,
        }
    });
}

/// Read every page again. Listed rows stay on screen until the last page
/// lands, then the new listing replaces them, keeping the highlight by id.
pub(super) fn refresh(app: &mut App, client: &Arc<Client>) {
    let page = &mut app.sessions_page;
    page.generation += 1;
    page.loading = true;
    page.reloading = !page.sessions.is_empty();
    page.incoming.clear();
    list(app, client, None);
    // Under the new generation too, or a projects answer still in flight is dropped.
    projects(app, client);
    super::leases::subscribe(app, client);
}

/// A change the page applied to its rows: a refresh still in flight may have
/// read the store before it, so it starts over.
pub(super) fn changed(app: &mut App, client: &Arc<Client>) {
    if app.sessions_page.reloading {
        refresh(app, client);
    }
}

/// One landed page: keep it, then ask the next until the bound.
pub(super) fn page(
    app: &mut App,
    client: &Arc<Client>,
    generation: u64,
    sessions: Vec<Session>,
    next: Option<String>,
) {
    if generation != app.sessions_page.generation {
        return;
    }
    // The rows index the list about to change: keep the highlight by id.
    let highlighted = super::highlighted_id(app);
    let page = &mut app.sessions_page;
    let into = match page.reloading {
        true => &mut page.incoming,
        false => &mut page.sessions,
    };
    let room = MAX_SESSIONS.saturating_sub(into.len());
    let overflow = sessions.len() > room;
    into.extend(sessions.into_iter().take(room));
    let full = into.len() >= MAX_SESSIONS;
    let truncated = overflow || (full && next.is_some());
    if let Some(cursor) = next.filter(|_| !full) {
        list(app, client, Some(cursor));
        if app.sessions_page.reloading {
            return;
        }
    } else {
        page.loading = false;
        if std::mem::take(&mut page.reloading) {
            page.sessions = std::mem::take(&mut page.incoming);
        }
    }
    app.sessions_page.truncated = truncated;
    super::relayout_on(app, highlighted);
}
