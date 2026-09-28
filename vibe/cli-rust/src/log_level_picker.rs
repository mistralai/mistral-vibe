//! `/log-level` picker state. Mirrors Python's `LogLevelPickerApp`: one row per
//! level with two toggleable badges, `session` and `config`.

use std::sync::Arc;

use serde_json::json;

use crate::app::App;
use crate::observability::level::{self, LogLevelChain, DEFAULT_LOG_LEVEL, LOG_LEVELS};
use crate::server::{method, Client};

pub const BADGE_SESSION: &str = "session";
pub const BADGE_CONFIG: &str = "config";

/// The config field the `config` badge writes.
const LOG_LEVEL_PATH: &str = "/log_level";

pub fn options() -> &'static [&'static str; 5] {
    &LOG_LEVELS
}

/// Open on the session override when set, else on the effective level.
pub fn open(app: &mut App) {
    let chain = level::get_log_level_chain();
    let highlighted = chain
        .session
        .clone()
        .unwrap_or_else(|| chain.effective.clone());
    app.log_level_picker.selected = index_of(&highlighted);
    app.log_level_picker.session = chain.session.clone();
    app.log_level_picker.config = chain.config.clone();
    app.log_level_picker.chain = chain;
    app.log_level_picker.focused_badge = BADGE_SESSION;
    app.log_level_picker.open = true;
}

pub fn navigate(app: &mut App, down: bool) {
    let last = options().len() - 1;
    if down {
        app.log_level_picker.selected = (app.log_level_picker.selected + 1).min(last);
    } else {
        app.log_level_picker.selected = app.log_level_picker.selected.saturating_sub(1);
    }
}

pub fn focus_badge(app: &mut App, badge: &'static str) {
    app.log_level_picker.focused_badge = badge;
}

/// Enter: set the focused badge to the highlighted level, or clear it if already there.
pub fn toggle_badge(app: &mut App) {
    let level = selected_level(app).to_owned();
    let picker = &mut app.log_level_picker;
    let slot = if picker.focused_badge == BADGE_SESSION {
        &mut picker.session
    } else {
        &mut picker.config
    };
    *slot = if slot.as_deref() == Some(level.as_str()) {
        None
    } else {
        Some(level)
    };
}

pub fn selected_level(app: &App) -> &'static str {
    options()[app.log_level_picker.selected.min(options().len() - 1)]
}

/// The chain recomputed against the draft badges, so the `›` marker and the
/// subtitle both move live while toggling (Python `_effective_level`).
pub fn draft_chain(app: &App) -> LogLevelChain {
    let picker = &app.log_level_picker;
    let env = picker.chain.env.clone();
    let effective = picker
        .session
        .clone()
        .or_else(|| env.clone())
        .or_else(|| picker.config.clone())
        .unwrap_or_else(|| DEFAULT_LOG_LEVEL.to_owned());
    LogLevelChain {
        session: picker.session.clone(),
        env,
        config: picker.config.clone(),
        effective,
    }
}

/// A committed config write's answer, applied on the main thread.
pub enum Event {
    Applied,
    /// The write failed; `restore` is the config tier as it stood before it.
    Failed {
        error: String,
        restore: Option<String>,
    },
}

pub fn apply_event(app: &mut App, event: Event) {
    if let Event::Failed { error, restore } = event {
        // Nothing else reconciles this tier -- `config/read` only runs at
        // startup -- so an unpersisted level must not stay in the chain.
        level::set_config_log_level(restore.as_deref());
        crate::ui::notice::show(app, &format!("Failed to persist log level: {error}"), 4);
    }
    app.commit_finished();
}

/// Esc applies: the session tier lands in-process, the config tier is persisted.
pub fn apply(app: &mut App, client: &Arc<Client>) {
    let picker = &app.log_level_picker;
    let session = picker.session.clone();
    let config = picker.config.clone();
    let previous = picker.chain.clone();
    let config_cleared = previous.config.is_some() && config.is_none();
    app.log_level_picker.open = false;

    level::set_session_override(session.as_deref());
    // Applied locally up front so the chain is right before the write lands;
    // `apply_event` puts it back if the write fails.
    level::set_config_log_level(config.as_deref());
    if config.is_some() || config_cleared {
        write_config(
            app,
            client,
            config.clone(),
            config_cleared,
            previous.config.clone(),
        );
    }
    let chain = level::get_log_level_chain();
    crate::ui::notice::show(app, &feedback(&previous, &chain, config_cleared), 4);
}

/// Python `on_log_level_picker_app_applied`: only report what actually moved.
fn feedback(previous: &LogLevelChain, chain: &LogLevelChain, config_cleared: bool) -> String {
    let mut parts: Vec<String> = Vec::new();
    match &chain.session {
        Some(level) => parts.push(format!("session override → {level}")),
        None if previous.session.is_some() => {
            parts.push("session override cleared".to_owned());
        }
        None => {}
    }
    if config_cleared {
        parts.push("config.toml cleared".to_owned());
    } else if let Some(level) = &chain.config {
        parts.push(format!("config.toml → {level}"));
    }
    if parts.is_empty() {
        parts.push("Log level unchanged".to_owned());
    }
    format!("{}  (effective: {})", parts.join("  "), chain.effective)
}

fn write_config(
    app: &mut App,
    client: &Arc<Client>,
    level: Option<String>,
    cleared: bool,
    restore: Option<String>,
) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let tx = app.log_level_picker.tx.clone();
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let op = if cleared {
            json!({"op": "remove", "path": LOG_LEVEL_PATH, "targetLayer": null})
        } else {
            json!({"op": "set", "path": LOG_LEVEL_PATH, "value": level, "targetLayer": null})
        };
        let params = json!({
            "sessionId": session_id,
            "ops": [op],
            "reason": "log level picker",
            "reloadRuntime": false,
        });
        let event = match client.request(method::CONFIG_WRITE, params).await {
            Ok(_) => Event::Applied,
            Err(error) => Event::Failed {
                error: error.to_string(),
                restore,
            },
        };
        crate::input::deliver(tx, event, &pending).await;
    });
}

fn index_of(level: &str) -> usize {
    options().iter().position(|it| *it == level).unwrap_or(0)
}
