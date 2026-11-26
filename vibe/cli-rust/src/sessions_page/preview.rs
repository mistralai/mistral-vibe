//! The highlighted session's transcript for the preview column: read once the
//! highlight rests, with the next row the way it moved, and kept for the last few sessions.

use std::collections::{HashSet, VecDeque};
use std::sync::Arc;
use std::time::Duration;

use serde_json::json;

use crate::app::App;
use crate::clock::UiInstant;
use crate::server::{method, Client, PublicSessionState, HISTORY_LIMIT};
use crate::transcript::{Snapshot, Transcript};
use crate::ui::markdown::MarkdownCache;
use crate::utils::transcript_cache::TranscriptCache;

/// How long the highlight rests before its session is read, so j/k never reads each row.
const SETTLE: Duration = Duration::from_millis(150);
/// Sessions whose transcript stays in memory, the least recently shown dropped first.
pub const CACHED: usize = 20;

/// One read transcript with its own render cache; `None` when the read failed.
pub struct Cached {
    pub id: String,
    /// The listing's `updatedAt` the read answered for: a newer one reads it again.
    pub updated_at: u64,
    pub transcript: Option<Transcript>,
    pub cache: TranscriptCache,
}

#[derive(Default)]
pub struct Preview {
    /// The most recently shown last; prefetched rows wait at the front until shown.
    pub sessions: VecDeque<Cached>,
    /// When to read the highlighted session; `None` once read.
    pub at: Option<UiInstant>,
    /// The session `at` waits on: a relayout that keeps it highlighted keeps the deadline.
    pending: Option<String>,
    /// The row the last rest read: the next rest prefetches one row further the same way.
    rested: Option<usize>,
    inflight: HashSet<String>,
    /// The session the markdown cache holds rows of: entry revisions repeat across transcripts.
    pub painted: Option<String>,
    pub markdown: MarkdownCache,
    /// The render cache of the current session, drawn from the live conversation.
    pub current: TranscriptCache,
    /// What the user did to the shown conversation: kept while it shows, reset for the next.
    pub view: super::preview_view::View,
}

pub struct Event {
    id: String,
    updated_at: u64,
    state: Option<Box<PublicSessionState>>,
}

impl Preview {
    /// A read that answered for this version of `id`; a failed one is tried again.
    fn fresh(&self, id: &str, updated_at: u64) -> bool {
        self.sessions.iter().any(|cached| {
            cached.id == id && cached.updated_at == updated_at && cached.transcript.is_some()
        })
    }

    /// The history of `id` as a snapshot, to paint while the session opens.
    pub fn snapshot(&self, id: &str) -> Option<Snapshot> {
        let cached = self.sessions.iter().find(|cached| cached.id == id)?;
        Some(cached.transcript.as_ref()?.snapshot())
    }

    /// The sessions being read, sorted.
    pub fn inflight(&self) -> Vec<String> {
        let mut ids: Vec<String> = self.inflight.iter().cloned().collect();
        ids.sort();
        ids
    }

    /// Mark `id` most recently shown: only drawing promotes.
    pub fn touch(&mut self, id: &str) {
        if let Some(index) = self.sessions.iter().position(|cached| cached.id == id) {
            if let Some(cached) = self.sessions.remove(index) {
                self.sessions.push_back(cached);
            }
        }
    }
}

/// Read the highlighted session once the highlight rests on it.
pub fn request(app: &mut App) {
    let wanted = super::selected(app).map(|session| session.id.clone());
    let preview = &mut app.sessions_page.preview;
    if wanted.is_none() {
        preview.at = None;
        preview.pending = None;
        return;
    }
    // Each landed page relayouts the list: the highlight resting on the same
    // session keeps its deadline instead of restarting it.
    if preview.at.is_some() && preview.pending == wanted {
        return;
    }
    preview.at = Some(crate::clock::now() + SETTLE);
    preview.pending = wanted;
}

/// Drop the read the highlight waits on: the page closed or opened a session.
pub fn cancel(app: &mut App) {
    let preview = &mut app.sessions_page.preview;
    preview.at = None;
    preview.pending = None;
}

/// The settle deadline passed: read the highlighted row, then the next one the way the highlight moved.
pub fn read_due(app: &mut App, client: &Arc<Client>) {
    cancel(app);
    let page = &mut app.sessions_page;
    if !page.open {
        return;
    }
    let selected = page.browse.list.selected();
    let ahead = match page.preview.rested.replace(selected) {
        Some(last) if selected > last => Some(selected + 1),
        Some(last) if selected < last => selected.checked_sub(1),
        _ => None,
    };
    let page = &app.sessions_page;
    let current = app.session.session_id.as_deref();
    let wanted: Vec<(String, u64)> = [Some(selected), ahead]
        .into_iter()
        .flatten()
        .filter_map(|option| page.sessions.get(*page.view.rows.get(option)?))
        // The current session previews its live conversation.
        .filter(|session| Some(session.id.as_str()) != current)
        .map(|session| (session.id.clone(), session.updated_at))
        .collect();
    for (id, updated_at) in wanted {
        read(app, client, id, updated_at);
    }
}

fn read(app: &mut App, client: &Arc<Client>, id: String, updated_at: u64) {
    let preview = &mut app.sessions_page.preview;
    if app.events.is_none() || preview.fresh(&id, updated_at) || preview.inflight.contains(&id) {
        return;
    }
    preview.inflight.insert(id.clone());
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params =
            json!({ "sessionId": id, "history": { "limit": HISTORY_LIMIT }, "turns": null });
        let state = client
            .request(method::SESSION_READ, params)
            .await
            .ok()
            .and_then(|value| serde_json::from_value(value["state"].clone()).ok());
        super::Event::Preview(Event {
            id,
            updated_at,
            state,
        })
    });
}

/// A read landed; a failed one is kept as such, so the column says so until a later rest reads again.
pub(super) fn apply_event(app: &mut App, event: Event) {
    app.sessions_page.preview.inflight.remove(&event.id);
    store(app, event.id, event.updated_at, event.state.as_deref());
}

/// Keep `state`'s history as the transcript of `id` (`None` for a failed read).
/// The highlighted session's goes in as the most recent, a prefetched one as
/// the least: past `CACHED`, the least recent goes. A prefetch makes its room
/// first, so it pushes out the least recently shown session, never itself.
pub fn store(app: &mut App, id: String, updated_at: u64, state: Option<&PublicSessionState>) {
    let shown = super::selected(app).is_some_and(|session| session.id == id);
    let preview = &mut app.sessions_page.preview;
    let transcript = state.map(|state| {
        let mut transcript = Transcript::default();
        transcript.load_snapshot(state);
        transcript
    });
    if preview.painted.as_deref() == Some(id.as_str()) {
        preview.painted = None;
    }
    let cached = Cached {
        id,
        updated_at,
        transcript,
        cache: TranscriptCache::default(),
    };
    // A session read again keeps its place; a new one goes by whether it shows.
    let kept = preview.sessions.iter().position(|old| old.id == cached.id);
    match (kept, shown) {
        (Some(index), _) => preview.sessions[index] = cached,
        (None, true) => preview.sessions.push_back(cached),
        (None, false) => {
            while preview.sessions.len() >= CACHED {
                preview.sessions.pop_front();
            }
            preview.sessions.push_front(cached);
        }
    }
    while preview.sessions.len() > CACHED {
        preview.sessions.pop_front();
    }
}
