//! What each rail chip shows: its glyph, the glyph's style, and its text, full or compact.

use ratatui::style::{Modifier, Style};
use ratatui::text::Span;

use crate::app::App;
use crate::artifacts::Standing;
use crate::goal::format;
use crate::rail::{self, Item};
use crate::server::GoalStatus;
use crate::ui::{goal_row, theme};

/// An item's glyph, the glyph's style, and the text after it: `count noun` with the noun muted, or the count alone when compact.
pub(super) fn content(
    app: &App,
    item: Item,
    compact: bool,
) -> (&'static str, Style, Vec<Span<'static>>) {
    match item {
        Item::Loops => next_loop(app, compact),
        Item::Agents => {
            let (running, listed) = rail::agent_counts(app);
            let (glyph, style, count) = match running {
                0 => ("○", theme::muted_style(), listed),
                _ => (spinner(app), theme::text(theme::primary()), running),
            };
            (glyph, style, counted(count, "agent", compact, fg()))
        }
        Item::Tasks => (
            "▪",
            theme::text(theme::text_accent()),
            counted(app.session.background_tasks.len(), "task", compact, fg()),
        ),
        Item::Prs => prs(app, compact),
        Item::Goal => goal(app, compact),
        Item::Plan => (
            "≡",
            theme::dim(theme::muted()),
            vec![Span::styled(
                crate::todo_tracker::progress_label(app.todo_tracker.todos()),
                fg(),
            )],
        ),
    }
}

/// `◷ loop in 4m` for the soonest loop, `◷ 4m` when compact; the list names them all.
fn next_loop(app: &App, compact: bool) -> (&'static str, Style, Vec<Span<'static>>) {
    let left = app.session.loops.first().map_or(0.0, |next| {
        next.next_fire_at - crate::clock::unix_now_seconds()
    });
    let when = crate::loops::schedule::next_run(left);
    let mut rest = Vec::new();
    if !compact {
        rest.push(Span::styled("loop ", theme::muted_style()));
    }
    let when = match compact {
        true => when.trim_start_matches("in ").to_owned(),
        false => when,
    };
    rest.push(Span::styled(when, fg()));
    ("◷", theme::muted_style(), rest)
}

/// The running-subagents glyph: a quarter-turn every 250ms while a turn runs, else still.
fn spinner(app: &App) -> &'static str {
    const FRAMES: [&str; 4] = ["◐", "◓", "◑", "◒"];
    match app.session.status {
        crate::app::Status::Generating { since } => {
            FRAMES[(crate::clock::since(since).as_millis() / 250 % 4) as usize]
        }
        _ => FRAMES[0],
    }
}

/// `● PR #123` with its state dot, or `● 2 PRs` counting every PR its window lists; unseen ones stand out until the list opens.
fn prs(app: &App, compact: bool) -> (&'static str, Style, Vec<Span<'static>>) {
    let shown: Vec<_> = app.session.artifacts.iter().collect();
    let unseen = shown.iter().any(|a| crate::artifacts::is_unseen(app, a));
    let number = match unseen {
        true => theme::text(theme::primary()).add_modifier(Modifier::BOLD),
        false => fg(),
    };
    let [artifact] = shown.as_slice() else {
        let noun = crate::artifacts::kinds(&shown);
        return (
            "●",
            theme::muted_style(),
            counted_as(shown.len(), noun, compact, number),
        );
    };
    let dot = theme::text(match crate::artifacts::standing(artifact) {
        Standing::Ready => theme::text_success(),
        Standing::Blocked => theme::text_error(),
        Standing::Merged => theme::merged(),
        Standing::Pending => theme::muted(),
    });
    let label = crate::artifacts::label(artifact);
    let mut rest = Vec::new();
    let id = match label.split_once(' ') {
        Some((noun, id)) => {
            if !compact {
                rest.push(Span::styled(format!("{noun} "), theme::muted_style()));
            }
            id.to_owned()
        }
        None => label,
    };
    rest.push(Span::styled(id, number));
    ("●", dot, rest)
}

/// `▶ goal 4m` while it runs, `‖ goal paused` once it stops, `✓ goal` when done; compact keeps the glyph and a running goal's time.
fn goal(app: &App, compact: bool) -> (&'static str, Style, Vec<Span<'static>>) {
    let Some(goal) = &app.goal.goal else {
        return ("▶", Style::default(), Vec::new());
    };
    let (glyph, glyph_style) = goal_row::icon(goal.status);
    let active = goal.status == GoalStatus::Active;
    let detail = match goal.status {
        GoalStatus::Active => format::duration(goal.time_used_seconds),
        GoalStatus::Complete => String::new(),
        _ => format::row_detail(goal),
    };
    let mut rest = Vec::new();
    if !compact {
        rest.push(Span::styled("goal ", theme::muted_style()));
    }
    if active || !compact {
        let style = if active { fg() } else { theme::muted_style() };
        rest.push(Span::styled(detail, style));
    }
    (glyph, glyph_style, rest)
}

/// `3 tasks` with the noun muted, or `3` when compact.
fn counted(count: usize, noun: &str, compact: bool, number: Style) -> Vec<Span<'static>> {
    let plural = if count == 1 { "" } else { "s" };
    counted_as(count, &format!("{noun}{plural}"), compact, number)
}

/// `2 PRs`: the count in `number`, then the already plural noun, muted.
fn counted_as(count: usize, noun: &str, compact: bool, number: Style) -> Vec<Span<'static>> {
    let mut spans = vec![Span::styled(count.to_string(), number)];
    if !compact {
        spans.push(Span::styled(format!(" {noun}"), theme::muted_style()));
    }
    spans
}

fn fg() -> Style {
    theme::text(theme::foreground())
}
