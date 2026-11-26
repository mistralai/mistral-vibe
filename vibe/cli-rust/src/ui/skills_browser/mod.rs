//! `/skills` bottom-app: the list under the highlighted skill's description, or a skill's page or version picker over its body.

mod body_lines;
pub(crate) use body_lines::wrap as wrap_body;
pub mod columns;
mod confirm_lines;
mod intro_lines;
mod list_lines;
mod page_lines;

use ratatui::layout::Rect;
use ratatui::Frame;

use super::browser_box::{self, draw_frame, GUTTER, SCROLLBAR_GUTTER};
use super::visual_lines::VisualLine;
use crate::app::App;

use crate::mouse::MouseTarget;
use crate::search_field::Search;
use crate::skills_browser::labels::{self, PLACEHOLDER, TITLE};
use crate::skills_browser::{self, list, Screen, State};

/// The lines between a page's rows and its body: a blank one and the rule.
const BODY_HEAD: usize = 2;
/// The fewest lines the box keeps under its title, so a page has room for its facts and some body.
const MIN_LINES: usize = 14;
/// The fewest lines a page keeps for its body, its rows taking the rest first.
const MIN_BODY: usize = 3;

/// The rows' lines, how many of them the box shows, and the lines above and under them.
struct Panes {
    intro: Vec<VisualLine>,
    rows: Vec<VisualLine>,
    shown_rows: usize,
    /// A page's or a picker's body, under a blank line and a rule; none on the list.
    shown_body: usize,
}

impl Panes {
    /// Fit the panes in `lines`, the box's pinned height: the list under its description, or a page or a picker over its body, which also gets the search row's line and grows up to `budget` (`50vh`) when its rows need it, so a long description never hides its facts.
    fn fit(state: &State, width: u16, lines: usize, budget: usize) -> Self {
        if state.page.is_some() && state.confirm.is_none() {
            let needed = Self::detail_rows(state, width, budget) + BODY_HEAD + MIN_BODY;
            return Self::detail(state, width, needed.clamp(lines + 1, budget.max(lines + 1)));
        }
        let intro = intro(state, width);
        let room = lines.saturating_sub(intro.len());
        let rows = browser_box::fit(width, room, |width| lay_out(state, width));
        let shown_rows = room;
        Self {
            intro,
            rows,
            shown_rows,
            shown_body: 0,
        }
    }

    /// The rows, a blank line and a rule, then the body in what is left, never under `MIN_BODY` lines.
    fn detail(state: &State, width: u16, budget: usize) -> Self {
        let room = budget.saturating_sub(BODY_HEAD + MIN_BODY);
        let rows = browser_box::fit(width, room, |width| lay_out(state, width));
        let shown_rows = rows.len().min(room);
        Self {
            intro: Vec::new(),
            rows,
            shown_rows,
            shown_body: budget.saturating_sub(BODY_HEAD + shown_rows),
        }
    }

    /// The lines a page's or a picker's rows take within `budget`.
    fn detail_rows(state: &State, width: u16, budget: usize) -> usize {
        browser_box::fit(width, budget, |width| lay_out(state, width)).len()
    }

    /// The list's own height within `budget` (`50vh`): its description and rows, never under `MIN_LINES`.
    fn wanted(state: &State, width: u16, budget: usize) -> usize {
        let intro = intro(state, width).len();
        let rows = browser_box::fit(width, budget, |width| lay_out(state, width)).len();
        (intro + rows).max(MIN_LINES).min(budget)
    }

    fn height(&self) -> usize {
        let body = match self.shown_body {
            0 => 0,
            lines => BODY_HEAD + lines,
        };
        self.intro.len() + self.shown_rows + body
    }
}

/// The highlighted skill's description over the list, unless a deletion waits or there is no skill to describe.
fn intro(state: &State, width: u16) -> Vec<VisualLine> {
    match state.confirm.is_none() && list::any_skill(state) {
        true => intro_lines::lay_out(state, usize::from(width.saturating_sub(GUTTER))),
        false => Vec::new(),
    }
}

/// The box's lines under its title on the list, pinned at the first draw on this screen height.
fn pinned(state: &mut State, width: u16, height: u16) -> usize {
    match state.pinned {
        Some((at, lines)) if at == height => lines,
        _ => {
            let lines = Panes::wanted(state, width, usize::from(height) / 2);
            state.pinned = Some((height, lines));
            lines
        }
    }
}

/// Draw the whole screen with the browser replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let lines = pinned(&mut app.skills_browser, area.width, area.height);
    let budget = usize::from(area.height) / 2;
    let panes = Panes::fit(&app.skills_browser, area.width, lines, budget);
    let help = skills_browser::help(&app.skills_browser);
    let height = panes.height() as u16 + browser_box::chrome(search_shown(app), area.width, &help);
    let kind = super::bottom_app::Kind::Skills;
    super::bottom_app::draw(app, f, area, height, kind, |app, f, area| {
        draw_box(app, f, area, &panes)
    });
}

/// The rows of the screen up, or the deletion's confirmation in their place while it waits.
fn lay_out(state: &State, width: usize) -> Vec<VisualLine> {
    match (&state.confirm, &state.page) {
        (Some(confirm), _) => confirm_lines::lay_out(confirm, width),
        (None, Some(page)) => page_lines::rows(state, page, width),
        (None, None) => list_lines::lay_out(state, width),
    }
}

/// The search row sits under the title in the list view and is hidden on a skill's page.
fn search_shown(app: &App) -> bool {
    app.skills_browser.page.is_none()
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, panes: &Panes) {
    let search = search_shown(app);
    let help = skills_browser::help(&app.skills_browser);
    let chrome = browser_box::chrome(search, area.width, &help);
    if area.height < chrome {
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
        super::search_field::draw_under_title(
            app,
            f,
            area,
            field,
            PLACEHOLDER,
            MouseTarget::Skills,
        );
    }
    let room = usize::from(area.height.saturating_sub(chrome));
    let intro = panes.intro.len().min(room) as u16;
    if intro > 0 {
        let pane = browser_box::pane(area, search, 0, intro);
        browser_box::draw_lines(app, f, area, pane, &panes.intro, 0, MouseTarget::Skills);
    }
    let shown_rows = panes
        .shown_rows
        .min(room.saturating_sub(usize::from(intro))) as u16;
    let rows = browser_box::pane(area, search, intro, shown_rows);
    app.skills_browser.list_area = rows;
    browser_box::draw_list(app, f, area, rows, &panes.rows, MouseTarget::Skills);
    let below = room.saturating_sub(usize::from(intro + shown_rows) + BODY_HEAD);
    let height = panes.shown_body.min(below) as u16;
    if height > 0 {
        let rule = browser_box::pane(area, search, intro + shown_rows + 1, 1);
        let line = [intro_lines::rule(
            labels::BODY_RULE,
            usize::from(area.width.saturating_sub(GUTTER)),
        )];
        browser_box::draw_lines(app, f, area, rule, &line, 0, MouseTarget::Skills);
        let body = browser_box::pane(area, search, intro + shown_rows + 2, height);
        draw_body(app, f, area, body);
    }
    browser_box::draw_help(f, area, &help);
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
