//! The loading line's spans: spinner or still glyph, label, elapsed time, hints and notes.

use std::time::Duration;

use ratatui::style::{Modifier, Style};
use ratatui::text::Span;

use super::{failure, format_elapsed, hint, WAITING_GLYPH};
use crate::app::{App, Status};
use crate::hints::Hint;
use crate::ui::theme;

/// The loading line's spans drawn at `width`; `None` when there is nothing to show or a startup failure shows instead.
pub(super) fn spans(app: &App, width: u16) -> Option<Vec<Span<'static>>> {
    let full = build(app, Some(width), true)?;
    if app.view.loading.egg.is_none() {
        return Some(full);
    }
    let rows = super::wrap::rows(full.clone(), width);
    let clipped = rows
        .first()
        .is_some_and(|row| row.width() > usize::from(width));
    if rows.len() == 1 && !clipped {
        return Some(full);
    }
    let plain = build(app, Some(width), false);
    // An easter egg never costs the line a row nor gets cut: the plain label shows instead.
    match plain {
        Some(plain) if clipped || rows.len() > super::wrap::rows(plain.clone(), width).len() => {
            Some(plain)
        }
        _ => Some(full),
    }
}

/// The spans with the plain label, which layout measures so an easter egg only fills room already free; its natural form without `width`.
pub(super) fn measured(app: &App, width: Option<u16>) -> Option<Vec<Span<'static>>> {
    build(app, width, false)
}

fn build(app: &App, width: Option<u16>, show_egg: bool) -> Option<Vec<Span<'static>>> {
    // The main loading row is hidden while a subagent is viewed, which
    // shows the subagent's own status instead (Running / Waiting for input).
    if let Some(child) = app.subagents.viewed_child() {
        let status = crate::subagents::subagent_loading_status(child.status)?;
        let anim = &app.view.loading;
        let color = |pos: usize| Style::default().fg(anim.color_at(pos));
        return Some(vec![
            Span::styled(anim.snake.render(), color(0)),
            Span::raw(" "),
            Span::styled(status.to_owned(), color(1)),
        ]);
    }
    if failure::rows(app, width.unwrap_or(0)) > 0 {
        return None;
    }
    // Queue mode's own keys replace every other hint for exactly as long as it lasts.
    let queue_hints = crate::input::composer_reachable(app)
        .then(|| crate::message_queue::mode_hints(app))
        .flatten();
    if !app.view.command_loading
        && !matches!(
            app.session.status,
            Status::Starting | Status::Generating { .. }
        )
    {
        let hints = queue_hints?;
        let mut spans = Vec::new();
        hint::push(&mut spans, String::new(), hints);
        return Some(spans);
    }
    let anim = &app.view.loading;
    // An agent that only waits for its background work gets a still, grey line
    // naming what it waits for. A program's sleep keeps the spinner: a message
    // waits for the program to end.
    let waiting = crate::background_work::only_waiting(app);
    let mut spans: Vec<Span<'static>> = match waiting {
        true => {
            let label = app.session.wait_target.as_deref().map_or_else(
                || anim.label().to_owned(),
                |target| format!("Waiting for {target}"),
            );
            waiting_head(&label)
        }
        false => {
            // Snake spinner glyph + gap, then the label one color-cycled span per
            // character, then the trailing "… ".
            let label = match show_egg {
                true => anim.shown(),
                false => anim.label(),
            };
            // The wipe runs over the egg's length; a plain label drawn in its place stretches onto it.
            let (drawn, wiped) = match show_egg {
                true => (1, 1),
                false => (label.chars().count() + 2, anim.total_elements()),
            };
            let color =
                |pos: usize| Style::default().fg(anim.color_at(pos * wiped.max(drawn) / drawn));
            let mut spans = vec![Span::styled(anim.snake.render(), color(0)), Span::raw(" ")];
            for (i, ch) in label.chars().enumerate() {
                spans.push(Span::styled(ch.to_string(), color(1 + i)));
            }
            spans.push(Span::styled("… ", color(1 + label.chars().count())));
            spans
        }
    };

    // Background slash commands have no timer or controls; manual shell commands do.
    // Startup offers no turn interruption; the queue header carries its cancellation.
    let background = app.view.command_loading && app.session.shell_operation_id.is_none();
    if background || app.session.status == Status::Starting {
        let hints = queue_hints.map(<[_]>::to_vec).unwrap_or_default();
        hint::push(&mut spans, String::new(), &hints);
        return Some(spans);
    }

    // Status is always `Generating` here (draw returned above otherwise).
    let elapsed = match (app.session.shell_started_at, app.session.status) {
        (Some(since), _) => anim.elapsed(since),
        (None, Status::Generating { since }) => anim.elapsed(since),
        _ => Duration::default(),
    };
    let hints = queue_hints.map_or_else(|| hint::running(app), <[_]>::to_vec);
    let timer = format!("{} ", format_elapsed(elapsed.as_secs()));
    hint::push(&mut spans, timer.clone(), &hints);
    // A callback is waiting for the user to stop typing.
    let note = (crate::question_app::waiting(app)
        || (app.approval.active.is_none() && app.approval.has_pending()))
    .then(|| {
        Span::styled(
            " typing detected, waiting…",
            Style::default().add_modifier(Modifier::DIM | Modifier::ITALIC),
        )
    });
    spans.extend(note.clone());
    match (waiting, width) {
        (true, Some(width)) => Some(shrink(spans, &timer, &hints, note, width)),
        _ => Some(spans),
    }
}

/// The still glyph and `label…`, muted.
fn waiting_head(label: &str) -> Vec<Span<'static>> {
    let muted = theme::muted_style();
    vec![
        Span::styled(WAITING_GLYPH, muted),
        Span::raw(" "),
        Span::styled(format!("{label}… "), muted),
    ]
}

/// A waiting line too wide for `width` first reads `Waiting…`, then drops its typing note, its hints, then its timer: it never wraps.
fn shrink(
    full: Vec<Span<'static>>,
    timer: &str,
    hints: &[Hint],
    note: Option<Span<'static>>,
    width: u16,
) -> Vec<Span<'static>> {
    let short = waiting_head("Waiting");
    let with = |hints: &[Hint], note: Option<Span<'static>>| {
        let mut spans = short.clone();
        hint::push(&mut spans, timer.to_owned(), hints);
        spans.extend(note);
        spans
    };
    let fits = |spans: &Vec<Span>| super::spans_width(spans) <= width;
    [full, with(hints, note), with(hints, None), with(&[], None)]
        .into_iter()
        .find(fits)
        .unwrap_or(short)
}
