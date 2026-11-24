//! The Sessions page: folders, their sessions and the highlighted one's conversation, in three columns.

mod facts;
mod folders;
mod help;
mod legend;
mod list;
mod preview;
mod rename;
mod row_text;
mod title;

use ratatui::layout::Rect;
use ratatui::Frame;

use super::{hint_line, search_field, theme};
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::mouse::{register_region, MouseTarget};
use crate::selection::{Folds, RegionId};

/// List rows the unfolded hints always leave.
const MIN_LIST_ROWS: u16 = 3;
/// From this terminal width the preview sits right of the list.
const WITH_PREVIEW: u16 = 80;
/// The preview's narrowest width, and its share of the room beside the folders.
const PREVIEW_MIN: u16 = 40;
const PREVIEW_PERCENT: u16 = 45;

/// The sessions column's keys, most useful first: the footer shows as many as fit.
const HINTS: &[Hint] = &[
    hints::NAVIGATE,
    (key::RIGHT_ENTER, action::OPEN),
    (key::LEFT, action::FOLDERS),
    ("n", action::NEW),
    hints::SEARCH,
    ("a", action::ARCHIVE),
    ("r", action::RENAME),
    (key::SPACE, action::FOLD),
    hints::CLOSE,
];
/// The sessions column's keys with no folders column to go to.
const LIST_HINTS: &[Hint] = &[
    hints::NAVIGATE,
    (key::RIGHT_ENTER, action::OPEN),
    ("n", action::NEW),
    hints::SEARCH,
    ("a", action::ARCHIVE),
    ("r", action::RENAME),
    (key::SPACE, action::FOLD),
    hints::CLOSE,
];
const FOLDER_HINTS: &[Hint] = &[
    hints::NAVIGATE,
    (key::RIGHT_ENTER, action::SESSIONS),
    ("n", action::NEW),
    hints::SEARCH,
    hints::CLOSE,
];
/// Pinned to the footer's right edge: `?` unfolds every key the row leaves out.
const MORE: Hint = ("?", action::MORE_KEYS);

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut().set_style(area, theme::screen_style());
    register_region(app, area, MouseTarget::SessionsPage);
    app.view.selection_region = crate::selection::Region::default();
    if area.height < 6 || area.width < 20 {
        return;
    }
    // The plan panel or debug console opening narrows the page without a resize.
    crate::sessions_page::folders::fitted(app, area.width);
    // A blank row above the title; the hints sit on the last row, like the
    // conversation's bottom bar.
    let inner = Rect::new(
        area.x + 2,
        area.y + 1,
        area.width.saturating_sub(4),
        area.height - 1,
    );
    title::draw(app, f, inner);
    let field = search_field::draw_row(
        f,
        Rect::new(inner.x, inner.y + 1, inner.width, 1),
        &app.sessions_page.browse.search,
        "Search sessions",
        app.view.cursor_on,
        theme::background(),
    );
    app.sessions_page.browse.search.area = field;
    // The footer grows upward when `?` unfolds it, keeping the highlighted row
    // and the ones around it in view.
    let room = inner.height.saturating_sub(5 + MIN_LIST_ROWS).max(1);
    let help = match app.sessions_page.help {
        true => help::lines(inner.width, room.into()),
        false => Vec::new(),
    };
    // Folded, the hints wrap like every footer: they are never clipped.
    let rows = match help.is_empty() {
        true => footer_rows(app, inner.width),
        false => help.len() as u16,
    };
    let footer_height = rows.clamp(1, room);
    // The filters row sits under the search, one blank row above the columns.
    title::draw_filters(app, f, Rect::new(inner.x, inner.y + 2, inner.width, 1));
    let body = Rect::new(
        inner.x,
        inner.y + 4,
        inner.width,
        inner.height.saturating_sub(5 + footer_height),
    );
    let columns = columns(app, area.width, body);
    app.sessions_page.folders_area = columns.folders.unwrap_or_default();
    // The list is one selectable region; the preview's conversation is its
    // own, selected like the transcript. The folders column is chrome.
    app.view.bottom_app_selection_region = super::selection::frame_region(columns.list);
    app.view.bottom_app_selection_chrome.clear();
    app.view.bottom_app_selection_halves = None;
    app.view.bottom_app_selection_folds = Folds::default();
    if let Some(folders) = columns.folders {
        folders::draw(app, f, folders);
        rule(f, Rect::new(folders.right() + 1, body.y, 1, body.height));
    }
    list::draw(app, f, columns.list);
    // The preview paints its conversation area again each frame; a column
    // dropped (a narrow terminal, no session) leaves none for clicks or the wheel.
    let view = &mut app.sessions_page.preview.view;
    view.area = Rect::default();
    view.entries.clear();
    view.selection = crate::selection::Region::default();
    if let Some(preview) = columns.preview {
        rule(f, Rect::new(preview.x - 1, body.y, 1, body.height));
        preview::draw(app, f, preview);
    }
    super::selection::overlay_region(app, f, RegionId::BottomApp);
    super::selection::overlay_region(app, f, RegionId::Preview);
    let footer_area = Rect::new(
        inner.x,
        inner.bottom() - footer_height,
        inner.width,
        footer_height,
    );
    match help.is_empty() {
        true => footer(app, f, footer_area),
        false => {
            for (offset, line) in help.iter().enumerate() {
                f.buffer_mut().set_line(
                    footer_area.x,
                    footer_area.y + offset as u16,
                    line,
                    footer_area.width,
                );
            }
        }
    }
    rename::draw(app, f, area);
}

/// Where each column goes in `body`; a narrow terminal drops the folders, then the preview.
struct Columns {
    folders: Option<Rect>,
    list: Rect,
    preview: Option<Rect>,
}

fn columns(app: &App, width: u16, body: Rect) -> Columns {
    // Decided from the page's width (`folders::fitted`).
    let folders = app.sessions_page.folders_shown.then_some(Rect {
        width: folders::WIDTH,
        ..body
    });
    let x = folders.map_or(body.x, |folders| folders.right() + 3);
    let rest = body.right().saturating_sub(x);
    // The preview describes the highlighted row: with no row, the list takes its room.
    let described = crate::sessions_page::selected(app).is_some();
    let preview = (described && width >= WITH_PREVIEW).then(|| {
        let wide = (rest * PREVIEW_PERCENT / 100)
            .max(PREVIEW_MIN)
            .min(rest / 2);
        Rect::new(body.right() - wide, body.y, wide, body.height)
    });
    let right = preview.map_or(body.right(), |preview| preview.x - 2);
    Columns {
        folders,
        list: Rect::new(x, body.y, right.saturating_sub(x), body.height),
        preview,
    }
}

fn rule(f: &mut Frame, area: Rect) {
    for y in area.y..area.bottom() {
        f.buffer_mut()
            .set_string(area.x, y, "│", theme::dim(theme::border_blurred()));
    }
}

/// The folded footer: the page's keys that fit one row
/// beside `? all keys & marks`; a row too narrow for that wraps the first few instead.
enum Footer {
    Wrapped(Vec<Hint>),
    Pinned(Vec<Hint>),
}

fn footer_hints(app: &App, width: u16) -> Footer {
    let page = &app.sessions_page;
    let search = &page.browse.search;
    let keys = match (
        page.folders_shown,
        crate::sessions_page::folders::focused(app),
    ) {
        (_, true) => FOLDER_HINTS,
        (true, false) => HINTS,
        (false, false) => LIST_HINTS,
    };
    let keys = crate::search_field::hints(search.focused, !search.query.is_empty(), keys);
    // The focused field types `?`, so it cannot unfold the keys then.
    if search.focused {
        return Footer::Wrapped(keys);
    }
    if let Some(row) = hints::fit(&keys, width, MORE) {
        return Footer::Pinned(row);
    }
    let (&close, rest) = keys.split_last().expect("the page always has Esc");
    let first = rest.iter().take(5).copied();
    Footer::Wrapped(first.chain([MORE, close]).collect())
}

/// The folded footer's rows: its keys, then one row of marks under the page's keys.
fn footer_rows(app: &App, width: u16) -> u16 {
    match footer_hints(app, width) {
        Footer::Wrapped(keys) => hint_line::height(&keys, width),
        Footer::Pinned(_) => 2,
    }
}

fn footer(app: &mut App, f: &mut Frame, area: Rect) {
    if app.overlays.notice.is_some() {
        return crate::ui::notice::draw(app, f, area, false);
    }
    match footer_hints(app, area.width) {
        Footer::Wrapped(keys) => hint_line::draw(f, area, &keys),
        Footer::Pinned(keys) => {
            hint_line::draw_pinned(f, area, &keys, MORE);
            if area.height > 1 {
                let marks = help::marks_line(area.width);
                f.buffer_mut()
                    .set_line(area.x, area.y + 1, &marks, area.width);
            }
        }
    }
}
