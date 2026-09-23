//! Effect (tool call) header and the dispatch into its result body.

use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::super::super::{pulse, theme};
use super::super::diff;
use super::effect_body::{push_edit_diff, push_effect_body, push_todo_body};
use super::expand_marker;
use crate::utils::{clean::clean_output, text};

pub(super) struct EffectView<'a> {
    /// Entry position and revision: the result-body cache key.
    pub index: usize,
    pub rev: u64,
    pub in_progress: bool,
    pub local: bool,
    pub expanded: bool,
    pub grouped: bool,
    /// The latest text appended to a running effect's `state.outputText`.
    pub stream_delta: Option<&'a str>,
}

/// A non-collapsible result has no disclosure header: its call row keeps the
/// settled indicator and its body is always rendered (Python `COLLAPSIBLE`).
fn settled_open(effect: &crate::server::EffectEntry, in_progress: bool) -> bool {
    !effect.is_collapsible() && !in_progress
}

/// Gutter width of the edit diff this effect renders, or `None` when it renders
/// none. The selection treats it as chrome (Python `DiffView._gutter_width`).
pub(super) fn diff_gutter(
    effect: &crate::server::EffectEntry,
    in_progress: bool,
    expanded: bool,
) -> Option<u16> {
    if !expanded && !settled_open(effect, in_progress) {
        return None;
    }
    let output = effect.file_edit_output()?;
    Some(diff::gutter_width(&diff::occurrences(&output)))
}

pub(super) fn push_effect(
    lines: &mut Vec<Line<'static>>,
    effect: &crate::server::EffectEntry,
    view: EffectView<'_>,
    attached_output: Option<&str>,
    pulse_frame: usize,
    width: u16,
    cache: Option<&mut crate::ui::markdown::MarkdownCache>,
) {
    let EffectView {
        index,
        rev,
        in_progress,
        local,
        expanded,
        grouped,
        stream_delta,
    } = view;
    let (verb, message, suffix) = effect.summary();
    let mut body = effect.body();
    if body.is_empty() {
        body = attached_output
            .map(|text| clean_output(text).lines().map(str::to_owned).collect())
            .unwrap_or_default();
    }
    let settled_open = settled_open(effect, in_progress);
    let has_body = effect.has_body() || !body.is_empty();
    let shown = (expanded && has_body) || settled_open;
    // Python `has_body`: a settled collapsible result with nothing to unfold
    // shows the inert marker in the disclosure slot instead of the fold triangle.
    let inert = !in_progress && effect.is_collapsible() && !has_body;
    let settled_marker = |glyph: &str| {
        if settled_open {
            glyph.to_string()
        } else if inert {
            "▪".to_string()
        } else {
            expand_marker(expanded).to_string()
        }
    };
    let (marker, marker_color) = if in_progress && local {
        ("✕".to_string(), theme::error())
    } else if in_progress {
        (pulse::glyph(pulse_frame).to_string(), theme::foreground())
    } else if effect.status() == Some("failed") {
        (settled_marker("✕"), theme::foreground())
    } else if effect.is_muted() {
        (settled_marker("□"), theme::muted())
    } else if effect.success() {
        (
            if (grouped && !inert) || effect.kind() == Some("user_question") {
                expand_marker(expanded).to_string()
            } else {
                settled_marker("✓")
            },
            theme::status_ready(),
        )
    } else {
        (settled_marker("✕"), theme::error())
    };
    let marker_style = if effect.is_muted() || inert {
        theme::muted_style()
    } else if effect.status() == Some("failed") {
        theme::dim(marker_color)
    } else {
        theme::text(marker_color)
    };
    let mut spans = vec![Span::styled(marker, marker_style)];
    let summary = |color| {
        if shown || (in_progress && local) {
            theme::text(color)
        } else {
            theme::dim(color)
        }
    };
    // An always-open result keeps the call row's own colors; a disclosure header
    // uses the muted section palette.
    let (verb_color, message_color) = if settled_open {
        (theme::primary(), theme::foreground())
    } else {
        (theme::effect_verb(), theme::effect_message())
    };
    if !verb.is_empty() {
        spans.push(Span::raw(" "));
        spans.push(Span::styled(
            verb.to_string(),
            summary(verb_color).add_modifier(Modifier::BOLD),
        ));
    }
    // Rows after the first hang under the message column, as Textual's header row does.
    let mut hanging = Vec::new();
    if !message.is_empty() {
        spans.push(Span::raw(" "));
        let indent: usize = spans.iter().map(|span| span.content.width()).sum();
        let style = summary(message_color);
        let mut rows = header_message(message, !in_progress, shown, width, indent).into_iter();
        spans.push(Span::styled(rows.next().unwrap_or_default(), style));
        hanging = rows
            .map(|row| {
                Line::from(vec![
                    Span::raw(" ".repeat(indent)),
                    Span::styled(row, style),
                ])
            })
            .collect();
    }
    // The suffix widget sits right after the header text, one cell in, muted
    // like the rest of the chrome (Python `.status-indicator-suffix`).
    if !suffix.is_empty() {
        spans.push(Span::raw(" "));
        spans.push(Span::styled(suffix.to_string(), theme::muted_style()));
    }
    lines.push(Line::from(spans));
    lines.extend(hanging);
    // The stream widget: a running call's latest `/state/outputText` append,
    // one gutter in (Python `tool-stream-message`, cleared on settle). It holds
    // the last patch's own delta, never the accumulated state text.
    if in_progress && !local {
        let stream_delta = clean_output(stream_delta.unwrap_or(""));
        if !stream_delta.is_empty() {
            for line in format!("→ {stream_delta}").lines() {
                lines.push(Line::from(vec![
                    Span::raw("  "),
                    Span::styled(text::expand_tabs(line), theme::muted_style()),
                ]));
            }
        }
    }
    if !shown {
        return;
    }
    match cache {
        Some(cache) => {
            let prepared = cache.prepare_lines(index, rev, width, theme::active_index(), || {
                let mut result = Vec::new();
                push_result(&mut result, effect, &body, width);
                result
            });
            lines.extend_from_slice(prepared.lines());
        }
        None => push_result(lines, effect, &body, width),
    }
}

/// The result body under the header: a pure function of the effect, its body, and width.
fn push_result(
    lines: &mut Vec<Line<'static>>,
    effect: &crate::server::EffectEntry,
    body: &[String],
    width: u16,
) {
    if let Some(output) = effect.file_edit_output() {
        push_edit_diff(
            lines,
            &diff::render_edit_diff(&diff::occurrences(&output), diff::language(&output.file)),
            width,
            effect.result_warnings(),
        );
        return;
    }
    if let Some(rows) = effect.todo_rows() {
        push_todo_body(lines, &rows, width);
        return;
    }
    let content_style = match effect
        .detail
        .as_ref()
        .and_then(|detail| detail.kind.as_deref())
    {
        // A read result is a fenced code block like a write result, so
        // unhighlightable content keeps the plain code colour.
        Some("file_read" | "file_write") => theme::text(theme::code_plain()),
        _ => theme::muted_style(),
    };
    // Python renders read and write results as fenced code blocks, so their
    // bodies highlight from the written or read file's extension.
    let lang = effect
        .file_write_path()
        .or(effect.file_read_path())
        .map(diff::language)
        .unwrap_or("");
    push_effect_body(
        lines,
        body,
        width,
        content_style,
        lang,
        effect.result_warnings(),
    );
}

/// Collapsed settled headers flatten to one ellipsized row; expanded ones keep their newlines.
fn header_message(
    message: &str,
    settled: bool,
    expanded: bool,
    width: u16,
    indent: usize,
) -> Vec<String> {
    let available = (width as usize).saturating_sub(indent);
    if !settled {
        return message.lines().map(str::to_owned).collect();
    }
    if !expanded {
        return vec![text::ellipsize(&text::single_line(message), available)];
    }
    text::multi_line(message)
        .lines()
        .flat_map(|line| text::wrap_hard(line, available))
        .collect()
}
