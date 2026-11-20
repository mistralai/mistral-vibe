//! The local skill deletion's confirmation, drawn in place of the rows until it is answered.

use ratatui::style::Modifier;

use super::body_lines::blank;
use crate::skills_browser::files::Confirm;
use crate::skills_browser::labels;
use crate::ui::visual_lines::{merge, text, wrap_indented, Sc, VisualLine};
use crate::ui::{list_cursor, theme};

const CANCEL: &str = " Cancel ";
const DELETE: &str = " Delete ";

/// The question in the error color, what deleting removes, then `Cancel` and `Delete` with the focused one highlighted.
pub(super) fn lay_out(confirm: &Confirm, width: usize) -> Vec<VisualLine> {
    let (base, dim) = list_cursor::styles(false);
    let error = base.fg(theme::text_error()).add_modifier(Modifier::BOLD);
    let question = labels::delete_question(&confirm.name);
    let mut lines = wrap_indented(&format!("  {question}"), error, width);
    lines.extend(wrap_indented(
        &format!(
            "  {}",
            labels::delete_warning(&confirm.path, confirm.linked)
        ),
        dim,
        width,
    ));
    let mut out: Vec<VisualLine> = lines.into_iter().map(line).collect();
    out.push(blank());
    out.push(line(buttons(confirm.delete_focused)));
    out
}

/// `  [ Cancel ]  [ Delete ]`: the focused one on the list cursor, an idle Delete in the error color.
fn buttons(delete_focused: bool) -> Vec<Sc> {
    let (base, _) = list_cursor::styles(false);
    let idle_delete = base.fg(theme::text_error());
    let (cancel, delete) = match delete_focused {
        true => (base, list_cursor::style()),
        false => (list_cursor::style(), idle_delete),
    };
    let mut chars = text("  ", base);
    chars.extend(text(CANCEL, cancel));
    chars.extend(text("  ", base));
    chars.extend(text(DELETE, delete));
    chars
}

fn line(chars: Vec<Sc>) -> VisualLine {
    VisualLine {
        option: None,
        highlighted: false,
        spans: merge(&chars),
    }
}
