//! The preview's own view of the conversation it shows: scrolled from its end, its folds, and where it was painted.

use std::collections::HashSet;

use ratatui::layout::Rect;

use crate::app::App;
use crate::selection::{Folds, Region, RowChrome, RowSpan};
use crate::transcript::Transcript;

/// Rows Shift+↑↓ and one wheel notch scroll the preview.
pub const STEP: u16 = 3;

#[derive(Default)]
pub struct View {
    /// The session this view is of: another one starts from its end, all folded.
    pub id: Option<String>,
    /// Rows scrolled up from the conversation's end; `0` shows the end.
    pub scroll: u16,
    /// Folded-open entries and groups, apart from the conversation's own.
    pub expanded: HashSet<String>,
    /// Whether every tool call shows (Ctrl+O), as in the conversation.
    pub tools_open: bool,
    /// The conversation's painted area last frame, for the wheel and clicks.
    pub area: Rect,
    /// `(top, bottom, header, id)` screen rows of each painted entry last frame.
    pub entries: Vec<(u16, u16, Option<u16>, String)>,
    /// The conversation's height last frame.
    pub total: u16,
    /// A click folded an entry since: the next frame keeps it under the pointer.
    pub pin: bool,
    /// What a drag over the conversation selects and copies, painted last frame:
    /// its region, then the transcript's chrome, `/raw` rows, wrap hangs and
    /// folds, the spacing rows between blocks, and the cells no copy takes
    /// (the pinned prompt).
    pub selection: Region,
    pub row_chrome: Vec<RowChrome>,
    pub raws: Vec<(u16, u16)>,
    pub hangs: Vec<RowSpan>,
    pub folds: Folds,
    pub margins: Vec<u16>,
    pub gaps: Vec<RowSpan>,
    /// The markdown links painted in the conversation last frame.
    pub links: Vec<crate::ui::markdown::Link>,
}

/// The link painted under `at` in the conversation, if any.
pub fn link_at(app: &App, at: (u16, u16)) -> Option<&crate::ui::markdown::Link> {
    let view = &app.sessions_page.preview.view;
    view.links.iter().find(|link| link.contains(at))
}

/// Whether `at` is on the painted conversation.
pub fn contains(app: &App, at: (u16, u16)) -> bool {
    app.sessions_page.preview.view.area.contains(at.into())
}

/// Scroll the conversation up (toward its start) or down; drawing clamps it.
pub fn scroll(app: &mut App, up: bool, rows: u16) {
    let view = &mut app.sessions_page.preview.view;
    view.scroll = match up {
        true => view.scroll.saturating_add(rows),
        false => view.scroll.saturating_sub(rows),
    };
}

/// Show the conversation `rows` up from its end, as its scrollbar sets it.
pub fn scroll_to(app: &mut App, rows: u16) {
    app.sessions_page.preview.view.scroll = rows;
}

/// Open the link painted at `at`: an inline image comes from the previewed
/// session's own conversation, not the attached one's.
pub fn open_link(app: &mut App, link: &crate::ui::markdown::Link) {
    if link.kind() != crate::ui::markdown::LinkKind::InlineImage {
        return crate::mouse::open_link(app, link);
    }
    let target = link.target().to_owned();
    with_shown(app, |transcript, _, _| {
        crate::inline_images::open_from(transcript, &target);
        false
    });
}

/// Fold open or shut the entry painted on `row`; whether one was there.
pub fn toggle_at(app: &mut App, row: u16) -> bool {
    let view = &app.sessions_page.preview.view;
    let Some((header, id)) = view
        .entries
        .iter()
        .find(|(top, bottom, _, _)| (*top..*bottom).contains(&row))
        .map(|(_, _, header, id)| (*header, id.clone()))
    else {
        return false;
    };
    // With a selection showing, a click clears it: only the header row folds,
    // as in the conversation.
    if app.selection.had_selection_at_press && header != Some(row) {
        return false;
    }
    with_shown(app, |transcript, view, _| {
        let toggled = transcript.toggle_fold(&mut view.expanded, &id).is_some();
        view.pin |= toggled;
        toggled
    })
}

/// Ctrl+O: every tool call of the shown conversation opens, or all fold back.
pub fn toggle_tools(app: &mut App) {
    with_shown(app, |transcript, view, details| {
        view.tools_open = !view.tools_open;
        view.expanded.clear();
        if view.tools_open {
            view.expanded.extend(transcript.expandable_ids());
            if details {
                view.expanded.extend(transcript.details_keys());
            }
        }
        true
    });
}

/// Run `change` on the highlighted session's conversation and the preview's
/// view; when it changed the folds, the conversation's layouts are redone.
fn with_shown(app: &mut App, change: impl FnOnce(&Transcript, &mut View, bool) -> bool) -> bool {
    let Some(id) = super::selected(app).map(|session| session.id.clone()) else {
        return false;
    };
    let details = app.view.collapse_turn_details;
    let preview = &mut app.sessions_page.preview;
    if app.session.session_id.as_deref() == Some(id.as_str()) {
        let changed = change(&app.view.transcript, &mut preview.view, details);
        if changed {
            preview.current.invalidate_layouts();
        }
        return changed;
    }
    let Some(cached) = preview.sessions.iter_mut().find(|cached| cached.id == id) else {
        return false;
    };
    let Some(transcript) = cached.transcript.as_ref() else {
        return false;
    };
    let changed = change(transcript, &mut preview.view, details);
    if changed {
        cached.cache.invalidate_layouts();
    }
    changed
}
