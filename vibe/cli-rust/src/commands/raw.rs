//! `/raw [on|off]`: show agent messages as their Markdown source, for terminal-friendly copying.

use super::submission::{new_message_id, NOTICE_TIMEOUT_SECS};
use crate::app::{App, Severity};
use crate::transcript::local;
use crate::ui;

const USAGE: &str = "Usage: /raw [on|off]";

pub(super) fn command(app: &mut App, args: &str) {
    let raw = match args.to_ascii_lowercase().as_str() {
        "" => !app.view.raw,
        "on" => true,
        "off" => false,
        _ => {
            local::add_command_error(&mut app.view.transcript, &new_message_id(), USAGE);
            return;
        }
    };
    set(app, raw);
    let notice = match raw {
        true => "Raw mode on: agent messages show their Markdown source",
        false => "Raw mode off: agent messages show formatted Markdown",
    };
    ui::notice::show(app, Severity::Information, notice, NOTICE_TIMEOUT_SECS);
}

/// Switch the display, keeping the viewport on the same entry as heights change.
fn set(app: &mut App, raw: bool) {
    if app.view.raw == raw {
        return;
    }
    app.view.raw = raw;
    crate::telemetry::raw_mode_toggled(app, raw);
    // Heights and table cells change, so a held selection would cover other text.
    app.selection.region = None;
    app.view.markdown_cache = ui::markdown::MarkdownCache::default();
    app.view.scroll_anchor = crate::utils::scroll::bulk_toggle_anchor(
        app.view.scroll_target,
        &app.view.entry_rows,
        app.view.sticky_prompt.map_or(0, |(_, rows)| rows),
    );
}
