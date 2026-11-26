//! Paints the status rail at the right of the loading row: chips shrink from the left, then drop, subagents and PRs last, when narrow.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use super::{hint_line, list_hover, theme};
use crate::app::App;
use crate::focus::Focus;
use crate::hints::{action, key};
use crate::rail::{self, Item};

/// Columns the loading text keeps at least before the rail takes the rest of its row.
const LOADING_MIN: u16 = 24;

/// Columns before the first chip, and the muted dot between two.
const LEAD: u16 = 2;
const SEP: &str = " · ";

mod chips;

use chips::content;

/// One painted item and its spans.
type Group = (Item, Vec<Span<'static>>);

/// Columns the rail needs within `max`, separators included; 0 when nothing fits.
pub fn width(app: &App, max: u16) -> u16 {
    layout(app, max, false).2
}

/// The rail's width on a loading row of `row` columns, `others` of them already taken; the loading text keeps all it shows unless chips need the room: a waiting line shrinks to `◷ Waiting…` before a chip does, and pinned chips take what their short form needs.
pub fn width_beside(app: &App, row: u16, others: u16) -> u16 {
    let free = row.saturating_sub(others);
    let items = rail::items(app);
    if items.is_empty() {
        return 0;
    }
    let full = super::loading::width(app).max(LOADING_MIN);
    // A waiting line shrinks before any chip; another line keeps its full width, and wraps only for pinned chips.
    let (keep, floor) = match super::loading::narrow_width(app) {
        Some(narrow) => (narrow, narrow),
        None => (full, super::loading::min_width(app).max(LOADING_MIN)),
    };
    let whole = total(&self::groups(app, &items, 0, false));
    if whole <= free.saturating_sub(full) {
        return width(app, free.saturating_sub(full));
    }
    if whole <= free.saturating_sub(keep) {
        return whole;
    }
    let (_, groups, width) = layout(app, free.saturating_sub(keep), false);
    let shown = |item: &Item| !pinned(*item) || groups.iter().any(|(painted, _)| painted == item);
    if items.iter().all(shown) {
        return width;
    }
    let kept: Vec<Item> = items.into_iter().filter(|item| pinned(*item)).collect();
    let needed = total(&self::groups(app, &kept, kept.len(), false));
    self::width(app, needed.min(free.saturating_sub(floor)))
}

/// Subagents and PRs stay on the rail: the other chips drop before them.
fn pinned(item: Item) -> bool {
    matches!(item, Item::Agents | Item::Prs)
}

/// Paint the rail on the last row of its loading-area column, against the box or window under it.
pub fn draw_in(app: &mut App, f: &mut Frame, column: Rect) {
    let row = match column.height {
        0 | 1 => Rect::default(),
        _ => Rect {
            y: column.bottom() - 1,
            height: 1,
            ..column
        },
    };
    draw(app, f, row);
}

/// Paint the rail into `area` and record where each item landed.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let focused = app.focus() == Focus::Rail;
    let keyboard = focused.then(|| rail::selected(app)).flatten();
    let active = keyboard.or_else(|| rail::open_item(app));
    app.rail.hits.clear();
    app.rail.area = area;
    app.rail.hidden = rail::items(app);
    if area.is_empty() {
        return;
    }
    let (hint, groups, _) = layout(app, area.width, focused && app.rail.letters);
    app.rail
        .hidden
        .retain(|item| !groups.iter().any(|(painted, _)| painted == item));
    let mut spans = Vec::new();
    let mut x = area.x;
    for (index, (item, group)) in groups.into_iter().enumerate() {
        let before = separator(index);
        x += before.width() as u16;
        spans.push(before);
        if index == 0 && hint {
            let lead = self::hint();
            x += spans_width(&lead);
            spans.extend(lead);
        }
        let width = spans_width(&group);
        app.rail.hits.push((item, x, width));
        x += width;
        spans.extend(group);
    }
    spans.push(Span::raw(" "));
    f.render_widget(Paragraph::new(Line::from(spans)), area);
    // Hit-tested against this frame's chips: a rail that moved never tints an empty cell.
    let hovered = rail::hovered(app).filter(|item| Some(*item) != active);
    let button = |item: Option<Item>| {
        let &(_, x, width) = app.rail.hits.iter().find(|hit| Some(hit.0) == item)?;
        Some(Rect { x, width, ..area })
    };
    // The keyboard's or the open window's chip is the button; any other chip under the mouse gets the faint hover.
    if let Some(button) = button(active) {
        list_hover::paint_button(f.buffer_mut(), button);
    }
    if let Some(chip) = button(hovered) {
        list_hover::tint(f.buffer_mut(), chip);
    }
}

/// The lead before the first chip, else the muted dot between two.
fn separator(index: usize) -> Span<'static> {
    match index {
        0 => Span::raw(" ".repeat(LEAD.into())),
        _ => Span::styled(SEP, theme::muted_style()),
    }
}

/// The dimmed `Ctrl+X to navigate` that leads the rail, then its separator.
fn hint() -> Vec<Span<'static>> {
    let mut spans = hint_line::inline_spans(&[(key::CTRL_X, action::NAVIGATE)]);
    for span in &mut spans {
        span.style = span.style.add_modifier(Modifier::DIM);
    }
    spans.push(Span::styled(SEP, theme::muted_style()));
    spans
}

/// Whether the hint leads, the chips that fit in `max`, and their width: the hint goes first, then the leftmost chips shrink to their short form, then drop, pinned ones last.
fn layout(app: &App, max: u16, letters: bool) -> (bool, Vec<Group>, u16) {
    let mut items = rail::items(app);
    // Once focused, the letters it announces take over; a week after the rail's last use, it comes back.
    if !app.rail.focused && !rail::hint_hidden(app) && !items.is_empty() {
        let groups = groups(app, &items, 0, letters);
        let width = total(&groups) + spans_width(&hint());
        if width <= max {
            return (true, groups, width);
        }
    }
    while !items.is_empty() {
        for compact in 0..=items.len() {
            let groups = groups(app, &items, compact, letters);
            let width = total(&groups);
            if width <= max {
                return (false, groups, width);
            }
        }
        let drop = items.iter().position(|item| !pinned(*item)).unwrap_or(0);
        items.remove(drop);
    }
    (false, Vec::new(), 0)
}

/// Columns `groups` take, separators and the trailing space included.
fn total(groups: &[Group]) -> u16 {
    groups
        .iter()
        .enumerate()
        .map(|(index, (_, spans))| separator(index).width() as u16 + spans_width(spans))
        .sum::<u16>()
        + 1
}

/// The chips for `items`, the first `compact` of them in their short form.
fn groups(app: &App, items: &[Item], compact: usize, letters: bool) -> Vec<Group> {
    items
        .iter()
        .enumerate()
        .map(|(index, &item)| (item, item_spans(app, item, index < compact, letters)))
        .collect()
}

fn spans_width(spans: &[Span]) -> u16 {
    spans.iter().map(|span| span.width() as u16).sum()
}

/// The chip's short form when asked and narrower, else its full one.
fn item_spans(app: &App, item: Item, compact: bool, letters: bool) -> Vec<Span<'static>> {
    let full = chip(app, item, false, letters);
    if !compact {
        return full;
    }
    let short = chip(app, item, true, letters);
    match spans_width(&short) < spans_width(&full) {
        true => short,
        false => full,
    }
}

/// `glyph text`; while the rail has focus the jump letter takes the glyph's place, so nothing moves.
fn chip(app: &App, item: Item, compact: bool, letters: bool) -> Vec<Span<'static>> {
    let (glyph, glyph_style, rest) = content(app, item, compact);
    let head = match letters {
        true => Span::styled(format!("{} ", item.letter()), hint_line::key_style()),
        false => Span::styled(format!("{glyph} "), glyph_style),
    };
    let mut spans: Vec<Span<'static>> = std::iter::once(head).chain(rest).collect();
    // A chip ends on its last visible cell, so only the separator ever sits between two chips.
    while let Some(last) = spans.last_mut() {
        let trimmed = last.content.trim_end().to_owned();
        if !trimmed.is_empty() {
            last.content = trimmed.into();
            break;
        }
        spans.pop();
    }
    spans
}
