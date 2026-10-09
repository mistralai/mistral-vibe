//! Effect (tool call) header and the dispatch into its result body.

use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::super::super::{pulse, theme};
use super::super::diff;
use super::bordered::{prefix, BORDER_WIDTH};
use super::effect_body::{push_edit_diff, push_effect_body, push_todo_body};
use super::expand_marker;
use crate::selection::{fold, Fold};
use crate::server::BodyLine;
use crate::ui::markdown::LinkedLines;
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
    lines: &mut LinkedLines,
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
    // Built only when shown: a collapsed row must not format its whole result every frame.
    let body = || {
        let body = effect.body();
        if !body.is_empty() {
            return body;
        }
        attached_output
            .map(|text| {
                clean_output(text)
                    .lines()
                    .map(|line| BodyLine::from(line.to_owned()))
                    .collect()
            })
            .unwrap_or_default()
    };
    let settled_open = settled_open(effect, in_progress);
    let has_body =
        effect.has_body() || attached_output.is_some_and(|text| !clean_output(text).is_empty());
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
            if grouped && !inert {
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
        spans.push(Span::styled(rows.next().unwrap_or_default().0, style));
        hanging = rows
            .enumerate()
            .map(|(index, (row, gap))| {
                // The gutter cells are prefixed once the body below is known.
                let hang = indent.saturating_sub(BORDER_WIDTH as usize);
                let line = Line::from(vec![Span::raw(" ".repeat(hang)), Span::styled(row, style)]);
                // The suffix ends the first row, so the message cannot fold across it.
                let gap = gap.filter(|_| index > 0 || suffix.is_empty());
                let hang = hang as u16;
                (line, Fold::hung(gap, hang).or(Some(Fold::indented(hang))))
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
    // Rows between the header and the body: the wrapped header, then the stream.
    let mut between = LinkedLines::default();
    for (line, fold) in hanging {
        between.push_folded(line, fold);
    }
    // The stream widget: a running call's latest `/state/outputText` append,
    // one gutter in (Python `tool-stream-message`, cleared on settle). It holds
    // the last patch's own delta, never the accumulated state text.
    if in_progress && !local {
        let stream_delta = clean_output(stream_delta.unwrap_or(""));
        if !stream_delta.is_empty() {
            for line in format!("→ {stream_delta}").lines() {
                between.push(Line::from(Span::styled(
                    text::expand_tabs(line),
                    theme::muted_style(),
                )));
            }
        }
    }
    let mut result = LinkedLines::default();
    match cache.filter(|_| shown) {
        Some(cache) => {
            let prepared = cache.prepare_lines(index, rev, width, theme::active_index(), || {
                let mut result = LinkedLines::default();
                push_result(&mut result, effect, &body(), width);
                result
            });
            result.append(prepared.linked().clone());
        }
        None if shown => push_result(&mut result, effect, &body(), width),
        None => {}
    }
    // A body's border climbs through those rows up to the header's arrow.
    let gutter = if result.is_empty() {
        Span::raw(" ".repeat(BORDER_WIDTH as usize))
    } else {
        prefix(false, theme::muted_style())
    };
    between.prefix(|_| gutter.clone());
    lines.append(between);
    lines.append(result);
}

/// The result body under the header: a pure function of the effect, its body, and width.
fn push_result(
    lines: &mut LinkedLines,
    effect: &crate::server::EffectEntry,
    body: &[BodyLine],
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
    // Python renders read, write, and scratchpad note results as fenced code
    // blocks: they highlight from the file's extension, and unhighlightable
    // content keeps the plain code colour.
    let code_path = effect.code_path();
    let fenced = code_path.is_some()
        || matches!(
            effect
                .detail
                .as_ref()
                .and_then(|detail| detail.kind.as_deref()),
            Some("file_read" | "file_write")
        );
    let content_style = if fenced {
        theme::text(theme::code_plain())
    } else {
        theme::muted_style()
    };
    let lang = code_path.map(diff::language).unwrap_or("");
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
) -> Vec<(String, Option<u16>)> {
    let available = (width as usize).saturating_sub(indent);
    if !settled {
        return message
            .lines()
            .map(|line| (line.to_owned(), None))
            .collect();
    }
    if !expanded {
        return vec![(
            text::ellipsize(&text::single_line(message), available),
            None,
        )];
    }
    text::multi_line(message)
        .lines()
        .flat_map(|line| fold::wrap_hard(line, available))
        .collect()
}
