//! `/skills` bottom-app: the list, a skill's page or its version picker, and the body under them.

mod body_lines;
pub(crate) use body_lines::wrap as wrap_body;
pub mod columns;
mod list_lines;
mod page_lines;

use ratatui::layout::Rect;
use ratatui::Frame;

use super::browser_box::{self, draw_frame, GUTTER, SCROLLBAR_GUTTER, SIDE_BY_SIDE};
use super::visual_lines::VisualLine;
use crate::app::App;
use crate::hints::{self, action, Hint};
use crate::mouse::MouseTarget;
use crate::search_field::Search;
use crate::skills_browser::labels::{self, PLACEHOLDER, TITLE};
use crate::skills_browser::{self, bodies, list, Screen, State};

/// Where the body sits: under the rows, beside them on a wide box, or nowhere.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Layout {
    Under,
    Beside,
    Alone,
}

/// The rows' lines, and how many of them and of the body's the box shows.
struct Panes {
    layout: Layout,
    rows: Vec<VisualLine>,
    shown_rows: usize,
    shown_body: usize,
}

impl Panes {
    /// Fit both panes in `budget` lines (`50vh`); a screen that keeps a body pane sizes it whatever the body and the highlight, so the rows never move as one loads or changes.
    fn fit(state: &State, width: u16, budget: usize) -> Self {
        match bodies::reserved(state) {
            true if width >= SIDE_BY_SIDE => Self::beside(state, width, budget),
            true => Self::stacked(state, width, budget, Layout::Under),
            false => Self::stacked(state, width, budget, Layout::Alone),
        }
    }

    /// The body a fixed half of `budget` under a blank line, or no body.
    fn stacked(state: &State, width: u16, budget: usize, layout: Layout) -> Self {
        let shown_body = match layout {
            Layout::Under => budget.saturating_sub(1) / 2,
            _ => 0,
        };
        let room = match shown_body {
            0 => budget,
            pane => budget.saturating_sub(1 + pane),
        };
        let rows = browser_box::fit(width, room, |width| lay_out(state, width));
        let shown_rows = rows.len().max(tallest_tab(state, width)).min(room);
        Self {
            layout,
            rows,
            shown_rows,
            shown_body,
        }
    }

    /// The rows on the left half and the body on the right, as tall as the rows but never under half of `budget`, so the body always has room.
    fn beside(state: &State, width: u16, budget: usize) -> Self {
        let left = width / 2;
        let rows = browser_box::fit(left, budget, |width| lay_out(state, width));
        let tallest = rows.len().max(tallest_tab(state, left));
        let shown = tallest.max(budget.saturating_sub(1) / 2).min(budget);
        Self {
            layout: Layout::Beside,
            rows,
            shown_rows: shown,
            shown_body: shown,
        }
    }

    /// The rows, with the body under a blank line or beside them.
    fn height(&self) -> usize {
        match self.layout {
            Layout::Under => self.shown_rows + 1 + self.shown_body,
            Layout::Beside | Layout::Alone => self.shown_rows,
        }
    }
}

/// Draw the whole screen with the browser replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let budget = area.height as usize / 2;
    let panes = Panes::fit(&app.skills_browser, area.width, budget);
    let height = panes.height() as u16 + browser_box::chrome(search_shown(app));
    let kind = super::bottom_app::Kind::Skills;
    super::bottom_app::draw(app, f, area, height, kind, |app, f, area| {
        draw_box(app, f, area, &panes)
    });
}

/// The list's lines on its tallest tab, so Tab never resizes the box; none off the list.
fn tallest_tab(state: &State, width: u16) -> usize {
    if state.page.is_some() {
        return 0;
    }
    let width = usize::from(width.saturating_sub(GUTTER));
    let tabs = state.tab_rows.iter();
    let heights = tabs.map(|(_, rows)| list_lines::height(rows, width));
    heights.max().unwrap_or(0)
}

fn lay_out(state: &State, width: usize) -> Vec<VisualLine> {
    match &state.page {
        Some(page) => page_lines::rows(state, page, width),
        None => list_lines::lay_out(state, width),
    }
}

/// The search row sits under the title in the list view and is hidden on a skill's page.
fn search_shown(app: &App) -> bool {
    app.skills_browser.page.is_none()
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, panes: &Panes) {
    let search = search_shown(app);
    if area.height < browser_box::chrome(search) {
        return;
    }
    let title = title(&app.skills_browser);
    draw_frame(f, area, &title);
    let tabs = match app.skills_browser.page {
        Some(_) => Vec::new(),
        None => list::tabs(&app.skills_browser),
    };
    let areas = browser_box::draw_tabs(f, area, &title, &tabs, app.skills_browser.tab);
    for (_, tab) in &areas {
        crate::mouse::register_region(app, *tab, MouseTarget::Skills);
    }
    app.skills_browser.tab_areas = areas;
    if search {
        browser_box::draw_search(app, f, area, field, PLACEHOLDER, MouseTarget::Skills);
    }
    let room = usize::from(area.height.saturating_sub(browser_box::chrome(search)));
    let shown_rows = panes.shown_rows.min(room) as u16;
    let (list_box, body_box) = match panes.layout {
        Layout::Beside => browser_box::halves(area),
        Layout::Under | Layout::Alone => (area, area),
    };
    let rows = browser_box::pane(list_box, search, 0, shown_rows);
    if panes.layout == Layout::Beside {
        browser_box::split_selection(app, (list_box, body_box));
    }
    app.skills_browser.list_area = rows;
    browser_box::draw_list(app, f, list_box, rows, &panes.rows, MouseTarget::Skills);
    let body = match panes.layout {
        Layout::Beside => Some(browser_box::pane(body_box, search, 0, shown_rows)),
        Layout::Under => {
            let below = room.saturating_sub(usize::from(shown_rows) + 1);
            let height = panes.shown_body.min(below) as u16;
            (height > 0).then(|| browser_box::pane(area, search, shown_rows + 1, height))
        }
        Layout::Alone => None,
    };
    if let Some(body) = body {
        draw_body(app, f, body_box, body);
    }
    let help = fitted(
        skills_browser::help(&app.skills_browser),
        area.width.saturating_sub(4),
    );
    browser_box::draw_help(f, area, &help);
}

/// A footer too wide for the box drops its navigation hint, then its Tab hint, so it still ends with Esc.
fn fitted(mut help: Vec<Hint>, width: u16) -> Vec<Hint> {
    for dropped in [action::NAVIGATE, action::SWITCH_TAB] {
        if hints::width(&help) <= width {
            break;
        }
        help.retain(|(_, action)| *action != dropped);
    }
    help
}

/// The body pane keeps its own scroll, wheel and scrollbar, and wraps short of the scrollbar.
fn draw_body(app: &mut App, f: &mut Frame, area: Rect, pane: Rect) {
    let width = usize::from(area.width.saturating_sub(SCROLLBAR_GUTTER));
    let lines = app.skills_browser.body_lines(width);
    let body = &mut app.skills_browser.pane;
    body.lines = lines.len();
    body.visible = usize::from(pane.height);
    body.scroll_to(body.scroll);
    let offset = body.scroll;
    let target = MouseTarget::SkillsBody;
    crate::mouse::register_region(app, pane, target);
    browser_box::draw_lines(app, f, area, pane, &lines, offset, target);
}

fn field(app: &mut App) -> &mut Search {
    &mut app.skills_browser.search
}

/// The box title: the list's, the open skill's name, or the scope its picker is for.
fn title(state: &State) -> String {
    match state.screen() {
        Screen::List => TITLE.to_owned(),
        Screen::Picker(page, picker) => labels::picker_title(page.subject.name(), picker.scope),
        Screen::Page(page) if page.subject.installed() => page.subject.name().to_owned(),
        Screen::Page(page) => labels::not_installed(page.subject.name()),
    }
}
