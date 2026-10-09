//! `/proxy-setup` drafts and app-server persistence, mirroring Python's ProxySetupApp.

use std::sync::Arc;

use ratatui::layout::Rect;
use serde_json::{json, Map, Value};

use crate::app::App;
use crate::chat_input::Action;
use crate::commands::{submission::new_message_id, CommandEvent};
use crate::server::proto_proxy::{ConfigProxyReadResponse, ProxySettingsView};
use crate::server::{method, Client};
use crate::transcript::local;
use crate::vibe_code_project::Field;

mod input;
pub use input::{copy_selection, handle_key, paste, press};

pub const SAVED_MESSAGE: &str = "Proxy settings saved. Restart the CLI for changes to take effect.";

/// One variable's input: its key, placeholder, saved value, and draft.
pub struct ProxyInput {
    pub key: String,
    pub description: String,
    pub initial: String,
    pub field: Field,
}

#[derive(Default)]
pub struct ProxySetupApp {
    pub open: bool,
    /// A `config/proxy/read` is in flight, so another `/proxy-setup` is dropped.
    pub loading: bool,
    pub inputs: Vec<ProxyInput>,
    pub focused: usize,
    /// Each visible input's index and text cells from the last render, so a click places the caret.
    pub input_areas: Vec<(usize, Rect)>,
    /// First visible line of the label/input rows when the box is too short for all of them.
    pub scroll: usize,
    /// Set by the wheel or scrollbar so the view stops following the focused input.
    pub free_scroll: bool,
    /// The session the settings were read for; a reply for another is dropped.
    pub session_id: Option<String>,
}

impl ProxySetupApp {
    pub fn new(settings: ProxySettingsView) -> Self {
        let inputs = settings
            .descriptions
            .into_iter()
            .map(|(key, description)| {
                let initial = settings
                    .values
                    .get(&key)
                    .cloned()
                    .flatten()
                    .unwrap_or_default();
                ProxyInput {
                    field: Field::new(initial.clone()),
                    description: description.as_str().unwrap_or_default().to_owned(),
                    initial,
                    key,
                }
            })
            .collect();
        Self {
            open: true,
            inputs,
            ..Self::default()
        }
    }

    /// Up/Down wrap around the inputs (Python `action_focus_next`/`_previous`).
    pub fn move_focus(&mut self, down: bool) {
        let len = self.inputs.len();
        if len == 0 {
            return;
        }
        self.focus((self.focused + if down { 1 } else { len - 1 }) % len);
    }

    /// Focusing an input selects its text, like Textual's `select_on_focus`.
    pub fn focus(&mut self, index: usize) {
        if let Some(input) = self.inputs.get_mut(index) {
            self.focused = index;
            self.free_scroll = false;
            input.field.edit(Action::SelectAll);
        }
    }

    /// Scroll the input rows by `lines`; the next render clamps the offset.
    pub fn wheel(&mut self, up: bool, lines: usize) {
        self.free_scroll = true;
        self.scroll = if up {
            self.scroll.saturating_sub(lines)
        } else {
            self.scroll.saturating_add(lines)
        };
    }

    /// The first visible line once clamped, following the focused input unless scrolled freely.
    pub fn reconcile_scroll(&mut self, visible: usize) -> usize {
        let total = self.inputs.len() * 2;
        let label = self.focused * 2;
        if !self.free_scroll && visible > 0 {
            let lowest = (label + 2).saturating_sub(visible);
            let highest = label + usize::from(visible < 2);
            self.scroll = self.scroll.clamp(lowest, highest);
        }
        self.scroll = self.scroll.min(total.saturating_sub(visible));
        self.scroll
    }

    /// The trimmed drafts that differ from the saved values; an empty one unsets.
    pub fn changes(&self) -> Map<String, Value> {
        self.inputs
            .iter()
            .filter_map(|input| {
                let value = input.field.text.trim();
                (value != input.initial).then(|| {
                    let value = if value.is_empty() {
                        Value::Null
                    } else {
                        Value::String(value.to_owned())
                    };
                    (input.key.clone(), value)
                })
            })
            .collect()
    }

    fn focused_field(&mut self) -> Option<&mut Field> {
        self.inputs
            .get_mut(self.focused)
            .map(|input| &mut input.field)
    }
}

/// Read the saved settings; the app opens once they land (`apply_read`).
pub fn open(app: &mut App, client: &Arc<Client>) {
    if app.proxy_setup.open || app.proxy_setup.loading {
        return;
    }
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.command_tx.clone())
    else {
        return;
    };
    app.proxy_setup.loading = true;
    app.proxy_setup.session_id = Some(session_id.clone());
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let result = client
            .request_err(method::CONFIG_PROXY_READ, json!({"sessionId": session_id}))
            .await
            .map_err(|error| error.message)
            .and_then(|value| {
                serde_json::from_value::<ConfigProxyReadResponse>(value)
                    .map(|response| response.settings)
                    .map_err(|error| error.to_string())
            });
        crate::input::deliver(Some(tx), CommandEvent::ProxySettings(result), &pending).await;
    });
}

/// Drop a reply for a replaced session, or one another modal raced ahead of.
pub fn apply_read(app: &mut App, result: Result<ProxySettingsView, String>) {
    let read_for = std::mem::take(&mut app.proxy_setup).session_id;
    if read_for != app.session.session_id || !crate::input::composer_reachable(app) {
        return;
    }
    let id = new_message_id();
    match result {
        Ok(settings) => {
            local::add_command_result(&mut app.view.transcript, &id, "Proxy setup opened...");
            app.proxy_setup = ProxySetupApp::new(settings);
        }
        Err(error) => local::add_command_error(
            &mut app.view.transcript,
            &id,
            &format!("Failed to read proxy settings: {error}"),
        ),
    }
}

fn cancel(app: &mut App) {
    app.overlays.last_escape = None;
    app.proxy_setup = ProxySetupApp::default();
    local::add_command_result(
        &mut app.view.transcript,
        &new_message_id(),
        "Proxy setup cancelled.",
    );
}

/// Enter writes every change, even none, as Python's `_save_and_close` does.
fn save(app: &mut App, client: &Arc<Client>) {
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.command_tx.clone())
    else {
        return;
    };
    let changes = app.proxy_setup.changes();
    app.proxy_setup = ProxySetupApp::default();
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let params = json!({"sessionId": session_id, "changes": changes});
        let event = match client.request_err(method::CONFIG_PROXY_WRITE, params).await {
            Ok(_) => CommandEvent::Result(SAVED_MESSAGE.to_owned()),
            Err(error) => CommandEvent::Error(format!(
                "Failed to apply: proxy settings — {}",
                error.message
            )),
        };
        crate::input::deliver(Some(tx), event, &pending).await;
    });
}
