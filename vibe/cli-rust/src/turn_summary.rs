//! Turn summary tracker and narrator manager state (Python `turn_summary`
//! and `narrator_manager`); the TTS/audio half lands separately.

use std::sync::Arc;

use serde_json::Value;
use tokio::sync::mpsc::Sender;

use crate::app::App;
use crate::server::{method, Client, NarrationSummarizeParams, NarrationSummarizeResponse};

/// One turn's accumulated data (Python `TurnSummaryData`).
#[derive(Debug, Default)]
pub struct TurnSummaryData {
    pub user_message: String,
    pub message_id: Option<String>,
    pub assistant_fragments: Vec<String>,
    pub error: Option<String>,
}

impl TurnSummaryData {
    /// Python joins the fragments with no separator.
    pub fn assistant_text(&self) -> String {
        self.assistant_fragments.concat()
    }
}

/// Accumulates one turn's data; each `start_turn` retires the previous turn's
/// data by bumping its generation (Python `TurnSummaryTracker`).
#[derive(Debug, Default)]
pub struct TurnSummaryTracker {
    data: Option<TurnSummaryData>,
    generation: u64,
}

impl TurnSummaryTracker {
    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn start_turn(&mut self, user_message: &str) {
        self.generation += 1;
        self.data = Some(TurnSummaryData {
            user_message: user_message.to_owned(),
            ..Default::default()
        });
    }

    pub fn track_user_message(&mut self, message_id: &str) {
        if let Some(data) = &mut self.data {
            data.message_id = Some(message_id.to_owned());
        }
    }

    pub fn track_assistant_text(&mut self, content: &str) {
        if let Some(data) = &mut self.data {
            if !content.is_empty() {
                data.assistant_fragments.push(content.to_owned());
            }
        }
    }

    pub fn set_error(&mut self, message: &str) {
        if let Some(data) = &mut self.data {
            data.error = Some(message.to_owned());
        }
    }

    pub fn cancel_turn(&mut self) {
        self.data = None;
    }

    /// Consume the turn's data, with the generation its summary must match.
    pub fn end_turn(&mut self) -> Option<(TurnSummaryData, u64)> {
        self.data.take().map(|data| (data, self.generation))
    }
}

/// Narrator manager state (Python `NarratorState`; `SPEAKING` arrives with the
/// TTS half).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum NarratorState {
    #[default]
    Idle,
    Summarizing,
}

/// A summarize answer, as it lands on the main thread.
pub enum Event {
    Summary {
        generation: u64,
        summary: Option<String>,
    },
}

/// Client-side narrator state (Python `NarratorManager` minus the audio half).
#[derive(Default)]
pub struct Narrator {
    pub state: NarratorState,
    pub summary: TurnSummaryTracker,
    /// Summary request in flight, aborted by `cancel` (Python `_cancel_summary`).
    cancel: Option<tokio::task::AbortHandle>,
    /// Animation frame of the `summarizing` row (Python `NarratorStatus._frame`).
    pub frame: usize,
    pub tx: Option<Sender<Event>>,
}

/// Python `NarratorManager.on_turn_start`. The empty user message matches
/// Python's live behavior: every server turn start routes through the
/// unsolicited path (`_begin_unsolicited_turn` passes `""`); the non-injected
/// `prompt_text` branch exists in Python (`_handle_turn`) but has no live call
/// site. A disabled narrator accumulates nothing (Python swaps in
/// `NoopTurnSummary`).
pub fn on_turn_start(app: &mut App, user_message: &str) {
    app.narrator.summary.start_turn(user_message);
    if !app.session.startup_config.narrator_enabled {
        app.narrator.summary.cancel_turn();
    }
}

pub fn on_user_message(app: &mut App, message_id: &str) {
    app.narrator.summary.track_user_message(message_id);
}

pub fn on_assistant_text(app: &mut App, content: &str) {
    app.narrator.summary.track_assistant_text(content);
}

pub fn on_turn_error(app: &mut App, message: &str) {
    app.narrator.summary.set_error(message);
}

pub fn on_turn_cancel(app: &mut App) {
    app.narrator.summary.cancel_turn();
}

/// Python `_track_narrator_event`, entryAdded half: the user entry's id and
/// the assistant entry's text feed the turn summary tracker.
pub fn track_narrator_added(app: &mut App, params: &Value) {
    let Some(entry) = params.get("entry") else {
        return;
    };
    if entry.get("type").and_then(Value::as_str) != Some("message") {
        return;
    }
    let Some(id) = entry.get("id").and_then(Value::as_str) else {
        return;
    };
    match entry.get("role").and_then(Value::as_str) {
        Some("user") => on_user_message(app, id),
        Some("assistant") => on_assistant_text(app, &entry_text(entry)),
        _ => {}
    }
}

/// Python `_track_narrator_event`, entryUpdated half: assistant text appends.
pub fn track_narrator_updated(app: &mut App, params: &Value) {
    let Some(id) = params.get("entryId").and_then(Value::as_str) else {
        return;
    };
    if !app.view.transcript.is_assistant_message(id) {
        return;
    }
    let ops = params.get("patch").and_then(Value::as_array);
    let delta = crate::transcript::patch::appended_text(
        ops.map(Vec::as_slice).unwrap_or(&[]),
        "/content/0/text",
    );
    if !delta.is_empty() {
        on_assistant_text(app, &delta);
    }
}

/// Python joins a message entry's text blocks with a blank line
/// (`PublicMessageEntry.text`).
fn entry_text(entry: &Value) -> String {
    entry
        .get("content")
        .and_then(Value::as_array)
        .map(|blocks| {
            blocks
                .iter()
                .filter(|block| block.get("type").and_then(Value::as_str) == Some("text"))
                .filter_map(|block| block.get("text").and_then(Value::as_str))
                .collect::<Vec<_>>()
                .join("\n\n")
        })
        .unwrap_or_default()
}

/// The failed turn's error message for the summary request. Python resolves a
/// friendly message (`_resolve_turn_error_message`); the raw wire message is
/// the closest the thin client carries.
pub fn turn_error_message(params: &Value) -> String {
    params
        .pointer("/turn/error/message")
        .and_then(Value::as_str)
        .or_else(|| params.pointer("/turn/error/code").and_then(Value::as_str))
        .unwrap_or_default()
        .to_owned()
}

/// Python `_complete_unsolicited_turn`: an `incomplete_stream` failure with no
/// queued server work auto-retries, and the retried turn's summary carries no
/// error.
pub fn retries_incomplete_stream(app: &App, params: &Value) -> bool {
    params.pointer("/turn/error/code").and_then(Value::as_str) == Some("incomplete_stream")
        && app.queue.is_empty()
}

/// Python `NarratorManager.on_turn_end`: request the turn's summary and show
/// the animated row while it is in flight. Gated on the config flag only; the
/// TTS half of Python's gate is out of scope here.
pub fn on_turn_end(app: &mut App, client: &Arc<Client>) {
    let Some((data, generation)) = app.narrator.summary.end_turn() else {
        return;
    };
    if !app.session.startup_config.narrator_enabled {
        return;
    }
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.narrator.tx.clone())
    else {
        return;
    };
    app.narrator.state = NarratorState::Summarizing;
    app.narrator.frame = 0;
    let client = client.clone();
    let handle = tokio::spawn(async move {
        let assistant_text = data.assistant_text();
        let summary = match serde_json::to_value(NarrationSummarizeParams {
            session_id,
            user_message: data.user_message,
            assistant_text,
            error: data.error,
            message_id: data.message_id,
        }) {
            Ok(params) => client
                .request(method::NARRATION_SUMMARIZE, params)
                .await
                .ok(),
            Err(err) => {
                tracing::warn!(%err, "narration/summarize params failed to serialize");
                None
            }
        }
        .and_then(|value| {
            serde_json::from_value::<NarrationSummarizeResponse>(value)
                .ok()
                .and_then(|response| response.summary)
        });
        let _ = tx
            .send(Event::Summary {
                generation,
                summary,
            })
            .await;
    });
    app.narrator.cancel = Some(handle.abort_handle());
}

/// Python `NarratorManager.cancel`: drop an in-flight summary request; returns
/// whether the narrator was active (Python `_try_interrupt_no_job_steps`).
pub fn cancel(app: &mut App) -> bool {
    let active = app.narrator.state != NarratorState::Idle;
    if let Some(handle) = app.narrator.cancel.take() {
        handle.abort();
    }
    app.narrator.state = NarratorState::Idle;
    active
}

/// Python `_on_turn_summary`: a stale generation or a `None` summary settles
/// the row; speaking the text is the TTS half and stays out of scope.
pub fn apply_event(app: &mut App, event: Event) {
    let Event::Summary { generation, .. } = event;
    app.narrator.cancel = None;
    if generation != app.narrator.summary.generation() {
        app.narrator.state = NarratorState::Idle;
        return;
    }
    app.narrator.state = NarratorState::Idle;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn start_turn_bumps_generation_and_reserves_fresh_data() {
        let mut tracker = TurnSummaryTracker::default();
        tracker.start_turn("first");
        tracker.track_assistant_text("partial");
        tracker.track_user_message("m1");
        assert_eq!(tracker.generation(), 1);
        tracker.start_turn("second");
        assert_eq!(tracker.generation(), 2);
        let (data, generation) = tracker.end_turn().unwrap();
        assert_eq!(generation, 2);
        assert_eq!(data.user_message, "second");
        assert!(data.assistant_fragments.is_empty());
        assert_eq!(data.message_id, None);
        assert_eq!(data.error, None);
    }

    #[test]
    fn assistant_fragments_join_in_order() {
        let mut tracker = TurnSummaryTracker::default();
        tracker.start_turn("hello");
        tracker.track_assistant_text("Hello ");
        tracker.track_assistant_text("big ");
        tracker.track_assistant_text("world");
        tracker.track_assistant_text("");
        let (data, _) = tracker.end_turn().unwrap();
        assert_eq!(data.assistant_text(), "Hello big world");
    }

    #[test]
    fn track_user_message_and_error_land_in_the_data() {
        let mut tracker = TurnSummaryTracker::default();
        tracker.start_turn("hello");
        tracker.track_user_message("m1");
        tracker.set_error("boom");
        let (data, _) = tracker.end_turn().unwrap();
        assert_eq!(data.message_id.as_deref(), Some("m1"));
        assert_eq!(data.error.as_deref(), Some("boom"));
    }

    #[test]
    fn cancel_turn_clears_data_and_end_turn_consumes_it() {
        let mut tracker = TurnSummaryTracker::default();
        tracker.start_turn("hello");
        tracker.track_assistant_text("text");
        tracker.cancel_turn();
        assert!(tracker.end_turn().is_none());
        tracker.start_turn("again");
        assert!(tracker.end_turn().is_some());
        assert!(tracker.end_turn().is_none());
    }

    #[test]
    fn feed_handlers_without_a_turn_are_noops() {
        let mut tracker = TurnSummaryTracker::default();
        tracker.track_user_message("m1");
        tracker.track_assistant_text("text");
        tracker.set_error("boom");
        assert!(tracker.end_turn().is_none());
    }

    #[test]
    fn disabled_narrator_accumulates_nothing() {
        let mut app = App::default();
        app.session.startup_config.narrator_enabled = false;
        on_turn_start(&mut app, "hello");
        on_assistant_text(&mut app, "text");
        on_user_message(&mut app, "m1");
        on_turn_error(&mut app, "boom");
        assert!(app.narrator.summary.end_turn().is_none());
        assert_eq!(app.narrator.summary.generation(), 1);
    }

    #[test]
    fn cancel_interrupts_summarizing_and_answer_settles_idle() {
        let mut app = App::default();
        app.session.startup_config.narrator_enabled = true;
        on_turn_start(&mut app, "hello");
        // `on_turn_end` needs a live client, so enter `Summarizing` directly.
        app.narrator.state = NarratorState::Summarizing;
        assert!(cancel(&mut app));
        assert_eq!(app.narrator.state, NarratorState::Idle);
        assert!(!cancel(&mut app));
        app.narrator.state = NarratorState::Summarizing;
        let generation = app.narrator.summary.generation();
        apply_event(
            &mut app,
            Event::Summary {
                generation,
                summary: Some("done".into()),
            },
        );
        assert_eq!(app.narrator.state, NarratorState::Idle);
    }

    #[test]
    fn stale_generation_summary_is_ignored() {
        let mut app = App::default();
        app.session.startup_config.narrator_enabled = true;
        on_turn_start(&mut app, "first");
        on_turn_start(&mut app, "second");
        apply_event(
            &mut app,
            Event::Summary {
                generation: 1,
                summary: Some("stale".into()),
            },
        );
        assert_eq!(app.narrator.state, NarratorState::Idle);
        // The current generation's summary also settles the row silently.
        apply_event(
            &mut app,
            Event::Summary {
                generation: 2,
                summary: None,
            },
        );
        assert_eq!(app.narrator.state, NarratorState::Idle);
    }
}
