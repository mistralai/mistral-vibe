//! Textual's `option-list--option-hover`: a faint background under the option the mouse is over.

use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::mouse::{self, MouseTarget};
use crate::option_list::OptionView;

/// Tint the hovered option's rows, unless it is the highlighted one.
pub(super) fn paint(app: &mut App, f: &mut Frame) {
    let Some(at) = app.view.mouse_position else {
        return;
    };
    if mouse::scrollbar_at(app, at) {
        return;
    }
    let Some(list) = mouse::target_at(app, at).and_then(|target| list(app, target)) else {
        return;
    };
    let Some(option) = list
        .index_at(at)
        .filter(|option| *option != list.selected())
    else {
        return;
    };
    let buffer = f.buffer_mut();
    for row in list.rows_of(option) {
        let row = row.intersection(buffer.area);
        if row.is_empty() {
            continue;
        }
        let base = buffer[(row.x, row.y)].bg;
        let hover = theme::block_hover_bg(base);
        for position in row.positions() {
            let cell = &mut buffer[position];
            if cell.bg == base {
                cell.set_bg(hover);
            }
        }
    }
}

/// The list under `target` that Python draws as a Textual `OptionList`.
fn list(app: &mut App, target: MouseTarget) -> Option<&dyn OptionView> {
    match target {
        MouseTarget::Approval | MouseTarget::Question | MouseTarget::SubagentList => None,
        MouseTarget::McpOAuth => Some(&app.mcp_oauth.list),
        MouseTarget::ConnectorAuth => Some(&app.connector_auth.list),
        _ => mouse::option_list(app, target).map(|list| &*list),
    }
}
