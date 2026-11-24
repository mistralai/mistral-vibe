//! Laying the list out again after any change, keeping the highlight on its session.

use super::{group, model, preview, SessionsPage, Trigger};
use crate::app::App;
use crate::sessions_page::Session;

/// Rebuild the lines after any change of filter or fold, keeping the highlighted session.
pub fn relayout(app: &mut App) {
    let highlighted = highlighted_id(app);
    relayout_on(app, highlighted);
}

/// The highlighted session's id, read before `sessions` changes: the view's rows
/// index the old list, so they name another session once it changed.
pub fn highlighted_id(app: &App) -> Option<String> {
    selected(app).map(|session| session.id.clone())
}

/// Rebuild the lines, keeping `highlighted` (read with `highlighted_id` before
/// `sessions` changed), then ask for the details of the row it lands on.
pub fn relayout_on(app: &mut App, highlighted: Option<String>) {
    let current_group = app
        .session
        .cwd
        .as_deref()
        .map(|cwd| current_group(&app.sessions_page, cwd));
    let page = &app.sessions_page;
    let browse = &page.browse;
    let query = model::Query {
        sort: browse.sort,
        search: &browse.search.query,
        folder: browse
            .folder_only
            .then_some(())
            .and(app.session.cwd.as_deref()),
        archived: browse.archived,
        gone: browse.gone,
        current_id: app.session.session_id.as_deref(),
        current_group,
        toggled: &browse.toggled,
        flat: page.folders_shown,
        group: browse.folder.as_deref(),
    };
    let view = model::build(&page.sessions, &page.projects, &query);
    let position = |id: &str| {
        view.rows
            .iter()
            .position(|&index| page.sessions[index].id == id)
    };
    // The session to land on (the one just opened) wins once it is listed,
    // or is given up once the open page's listing is complete without it.
    let pending = browse.highlight.as_deref().and_then(position);
    let settled = pending.is_some() || (page.open && !page.loading);
    // At startup the current session is the fresh one: offer the latest other one.
    let current = app.session.session_id.as_deref();
    let target = match (pending, highlighted.as_deref(), page.trigger) {
        (Some(option), _, _) => Some(option),
        (None, Some(id), _) => position(id),
        (None, None, Trigger::Startup) => view
            .rows
            .iter()
            .position(|&index| Some(page.sessions[index].id.as_str()) != current),
        (None, None, _) => current.and_then(position),
    };
    let last = view.rows.len().saturating_sub(1);
    let option = target.unwrap_or(browse.list.selected().min(last));
    let page = &mut app.sessions_page;
    // A folder the filters empty shows All meanwhile; it comes back with its sessions.
    let folder = view.folder;
    page.view = view;
    page.browse
        .folders
        .highlight(folder.map_or(0, |index| index + 1));
    page.browse.list.highlight(option);
    if settled {
        page.browse.highlight = None;
    }
    // A highlight moved without a key (archive, fold, a paste into search)
    // still shows its preview; a closed page reads none.
    if page.open {
        preview::request(app);
    }
}

/// The group of the current folder: the group of a listed session there, else the folder's own.
fn current_group(page: &SessionsPage, cwd: &str) -> group::Group {
    page.sessions
        .iter()
        .find(|session| session.cwd.as_deref() == Some(cwd))
        .map(|session| group::of(session, &page.projects))
        .unwrap_or_else(|| group::of_folder(cwd, &page.projects))
}

/// The highlighted session, if any row is shown.
pub fn selected(app: &App) -> Option<&Session> {
    let page = &app.sessions_page;
    let index = *page.view.rows.get(page.browse.list.selected())?;
    page.sessions.get(index)
}
