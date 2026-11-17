//! The startup warning inside tmux with `mouse off`: how to scroll, and Ctrl+N to stop showing it.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::app::App;
use crate::hints::key;
use crate::utils::cache_store;

const CACHE_SECTION: &str = "tmux_mouse_hint";
const CACHE_KEY: &str = "dismissed";

/// The warning as markdown, its command, file and keys in code spans.
pub fn text() -> String {
    format!(
        "tmux has mouse off, so the mouse wheel scrolls the terminal, not this conversation. Add `set -g mouse on` to `~/.tmux.conf`, or scroll with `{}`. Press `{}` to stop showing this.",
        key::SHIFT_ARROWS,
        key::CTRL_N
    )
}

/// File the warning in the open startup box when tmux took the mouse capture the setting asked for, unless dismissed before.
pub fn show(app: &mut App) {
    let text = text();
    if !app.session.startup_config.enable_mouse_capture
        || !crate::tmux::mouse_off()
        || !crate::notices::startup_open(app)
        || crate::notices::startup_lists(app, &text)
        || dismissed()
    {
        return;
    }
    crate::notices::warn_markdown(app, &text);
    crate::telemetry::tmux_mouse_hint_rendered(app);
}

/// Drop the warning once its advice no longer applies: tmux's mouse is on, or mouse capture is off.
pub fn refresh(app: &mut App) {
    if !crate::tmux::mouse_off() || !app.session.startup_config.enable_mouse_capture {
        crate::notices::withdraw(app, &text());
    }
}

/// Ctrl+N while the warning is on screen: remove it and never show it again.
pub fn handle_key(app: &mut App, key: &KeyEvent) -> bool {
    if key.modifiers != KeyModifiers::CONTROL || key.code != KeyCode::Char('n') {
        return false;
    }
    if !crate::notices::withdraw(app, &text()) {
        return false;
    }
    cache_store::write_int(CACHE_SECTION, CACHE_KEY, 1);
    crate::telemetry::tmux_mouse_hint_disabled(app, "ctrl_n");
    true
}

fn dismissed() -> bool {
    cache_store::read_int(CACHE_SECTION, CACHE_KEY).is_some_and(|value| value != 0)
}
