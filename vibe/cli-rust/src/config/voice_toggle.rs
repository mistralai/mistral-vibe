//! `vibe.voice_mode_toggled` for `/config` writes (Python `_persist_voice_settings`).

use crate::app::App;
use crate::config_fields::{ConfigField, Loaded};

/// A reload whose `voice_mode_enabled` differs from the shown value means a
/// write flipped it; the first load has no prior value and records nothing.
pub(super) fn record(app: &App, loaded: &Loaded) {
    let before = voice_mode(&app.config_screen.fields);
    let after = voice_mode(&loaded.fields);
    if let (Some(before), Some(after)) = (before, after) {
        if before != after {
            crate::telemetry::voice_mode_toggled(app, after);
        }
    }
}

fn voice_mode(fields: &[ConfigField]) -> Option<bool> {
    fields
        .iter()
        .find(|field| field.path == "/voice_mode_enabled")
        .and_then(|field| field.raw_value.as_bool())
}
