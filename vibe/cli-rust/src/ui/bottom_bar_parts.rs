//! The bottom bar's styled parts: each tone's style.

use ratatui::style::Style;
use ratatui::text::Span;

use super::footer_fit::{Form, Tone};
use super::theme;

pub fn spans(form: Form) -> Vec<Span<'static>> {
    form.into_iter()
        .map(|(text, tone)| Span::styled(text, style(tone)))
        .collect()
}

fn style(tone: Tone) -> Style {
    match tone {
        Tone::Muted => theme::muted_style(),
        Tone::Text => theme::text(theme::foreground()),
        Tone::Gauge => gauge(theme::gauge_fill()),
        Tone::GaugeWarn => gauge(theme::warning()),
        Tone::GaugeAlarm => gauge(theme::error()),
    }
}

fn gauge(fill: ratatui::style::Color) -> Style {
    theme::text(fill).bg(theme::gauge_track())
}
