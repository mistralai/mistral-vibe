//! The loading line's spans: spinner or still glyph, label, elapsed time, hints and notes.

use std::time::Duration;

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::Span;

use super::{failure, format_elapsed, hint, idle, RESUMING_LOADING_STATUS, WAITING_GLYPH};
use crate::app::{App, Status};
use crate::ui::theme;

/// The loading line's spans, or `None` when there is nothing to show or a startup failure shows instead.
pub(super) fn spans(app: &App, area: Rect) -> Option<Vec<Span<'static>>> {
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
    if failure::rows(app, area.width) > 0 {
        return None;
    }
    // Queue mode's own keys replace every other hint for exactly as long as it lasts.
    let queue_hints = crate::input::composer_reachable(app)
        .then(|| crate::message_queue::mode_hints(app))
        .flatten();
    let resuming = crate::resume_picker::spinning(app);
    if !app.view.command_loading
        && !resuming
        && !matches!(
            app.session.status,
            Status::Starting | Status::Generating { .. }
        )
    {
        if let Some(hints) = queue_hints {
            let mut spans = Vec::new();
            hint::push(&mut spans, String::new(), hints);
            return Some(spans);
        }
        return idle::next_loop(app, area.width);
    }
    let anim = &app.view.loading;
    // An agent that only waits for its background work gets a still, grey line
    // naming what it waits for. A program's sleep keeps the spinner: a message
    // waits for the program to end.
    let waiting = app.session.shell_operation_id.is_none()
        && app
            .session
            .active_turn_id
            .as_deref()
            .is_some_and(|turn| app.view.transcript.only_model_waiting(turn));
    let mut spans: Vec<Span<'static>> = match waiting {
        true => {
            let label = app.session.wait_target.as_deref().map_or_else(
                || anim.label.clone(),
                |target| format!("Waiting for {target}"),
            );
            let muted = theme::muted_style();
            vec![
                Span::styled(WAITING_GLYPH, muted),
                Span::raw(" "),
                Span::styled(format!("{label}… "), muted),
            ]
        }
        false => {
            let color = |pos: usize| Style::default().fg(anim.color_at(pos));
            // Snake spinner glyph + gap, then the label one color-cycled span per
            // character, then the trailing "… ".
            let label = if resuming {
                RESUMING_LOADING_STATUS
            } else {
                &anim.label
            };
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
    let background =
        (app.view.command_loading || resuming) && app.session.shell_operation_id.is_none();
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
    hint::push(
        &mut spans,
        format!("{} ", format_elapsed(elapsed.as_secs())),
        &hints,
    );
    // A message reaches an agent that only waits at once: say so after the controls.
    if waiting && !app.queue.paused && crate::input::composer_reachable(app) {
        spans.push(Span::styled(
            format!(" · send a message to {}", crate::hints::action::STEER),
            crate::ui::hint_line::action_style(),
        ));
    }

    // A callback is waiting for the user to stop typing.
    if crate::question_app::waiting(app)
        || (app.approval.active.is_none() && app.approval.has_pending())
    {
        spans.push(Span::styled(
            " typing detected, waiting…",
            Style::default().add_modifier(Modifier::DIM | Modifier::ITALIC),
        ));
    }
    Some(spans)
}
