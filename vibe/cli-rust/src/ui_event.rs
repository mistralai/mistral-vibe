//! The one channel carrying every widget's async answer back to the main thread.

use std::future::Future;
use std::sync::Arc;

use tokio::sync::mpsc;

use crate::app::App;
use crate::server::Client;
use crate::{
    agents, approval, connector_auth, debug_console, feedback, log_level_picker, mcp, mcp_oauth,
    message_queue, model_picker, older_history, paste_image, question_app, resume_picker, rewind,
    subagents, theme_picker, thinking_picker, turn_summary,
};

/// Room for every answer in flight at once; senders await rather than drop.
pub const CHANNEL_CAP: usize = 64;

pub type Sender = mpsc::Sender<UiEvent>;

macro_rules! ui_events {
    ($($variant:ident($ty:ty)),* $(,)?) => {
        pub enum UiEvent {
            $($variant($ty),)*
        }
        $(impl From<$ty> for UiEvent {
            fn from(event: $ty) -> Self {
                Self::$variant(event)
            }
        })*
    };
}

ui_events! {
    Theme(theme_picker::Event),
    Model(model_picker::Event),
    LogLevel(log_level_picker::Event),
    Thinking(thinking_picker::Event),
    Agents(agents::Event),
    Resume(resume_picker::Event),
    Rewind(rewind::Event),
    Mcp(mcp::Event),
    McpOAuth(mcp_oauth::Event),
    ConnectorAuth(connector_auth::Event),
    Queue(message_queue::QueueEvent),
    Approval(approval::Event),
    Question(question_app::Event),
    Feedback(feedback::Event),
    PasteImage(paste_image::Event),
    Narrator(turn_summary::Event),
    Subagents(subagents::Event),
    OlderHistory(older_history::Event),
    DebugConsole(debug_console::Event),
}

/// Run `task` off the main thread and hand its answer back, holding the app busy until it lands.
pub fn spawn<E>(app: &App, task: impl Future<Output = E> + Send + 'static)
where
    E: Into<UiEvent> + Send + 'static,
{
    let tx = app.events.clone();
    let pending = app.commit_started();
    tokio::spawn(async move { crate::input::deliver(tx, task.await, &pending).await });
}

/// Apply one answer on the main thread, first releasing the commit its sender claimed.
pub fn apply(app: &mut App, client: &Arc<Client>, event: UiEvent) {
    if claims_commit(&event) {
        app.commit_finished();
    }
    match event {
        UiEvent::Theme(event) => theme_picker::apply_event(app, event),
        UiEvent::Model(event) => model_picker::apply_event(app, event),
        UiEvent::LogLevel(event) => log_level_picker::apply_event(app, event),
        UiEvent::Thinking(event) => thinking_picker::apply_event(app, event),
        UiEvent::Agents(event) => agents::apply_event(app, client, event),
        UiEvent::Rewind(event) => rewind::apply_event(app, event),
        UiEvent::Mcp(event) => mcp::apply_event(app, client, event),
        UiEvent::McpOAuth(event) => mcp_oauth::apply_event(app, client, event),
        UiEvent::ConnectorAuth(event) => connector_auth::apply_event(app, client, event),
        UiEvent::Feedback(event) => feedback::apply_event(app, event),
        UiEvent::Narrator(event) => turn_summary::apply_event(app, event),
        UiEvent::Resume(event) => resume_picker::apply_event(app, client, event),
        UiEvent::Queue(event) => {
            if let Some(session) = message_queue::apply_event(app, client, event) {
                feedback::maybe_show(app, client, Some(session));
            }
        }
        UiEvent::Approval(event) => approval::apply_event(app, event),
        UiEvent::Question(event) => question_app::apply_event(app, event),
        UiEvent::PasteImage(event) => paste_image::apply_event(app, event),
        UiEvent::Subagents(event) => subagents::apply_event(app, event),
        UiEvent::OlderHistory(event) => older_history::apply_event(app, event),
        UiEvent::DebugConsole(event) => debug_console::apply_event(app, client, event),
    }
}

/// Narration and debug-console polls are the only answers sent without claiming a commit.
fn claims_commit(event: &UiEvent) -> bool {
    !matches!(
        event,
        UiEvent::Narrator(_) | UiEvent::DebugConsole(debug_console::Event::Polled(..))
    )
}
