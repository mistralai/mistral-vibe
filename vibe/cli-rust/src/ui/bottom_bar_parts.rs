//! The bottom bar's styled parts: the PR/MR link input and each tone's style.

use ratatui::style::Style;
use ratatui::text::Span;

use super::bottom_bar::BarLinkAction;
use super::footer_fit::{Form, LinkInput, Tone};
use super::theme;
use crate::app::App;
use crate::artifacts::Standing;

/// The PR/MR link: the latest artifact when alone, else a count that opens the modal.
pub fn link_input(app: &App) -> Option<(LinkInput, BarLinkAction)> {
    match app.session.artifacts.as_slice() {
        [] => None,
        [artifact] => Some((
            LinkInput {
                label: crate::artifacts::label(artifact),
                short: crate::artifacts::short_label(artifact),
                status: crate::artifacts::status_label(artifact),
                dot: Some(standing_tone(crate::artifacts::standing(artifact))),
            },
            BarLinkAction::OpenUrl(artifact.external_url.clone()),
        )),
        artifacts => {
            let label = crate::artifacts::count_label(artifacts);
            Some((
                LinkInput {
                    short: label.clone(),
                    label,
                    status: None,
                    dot: None,
                },
                BarLinkAction::ListArtifacts,
            ))
        }
    }
}

/// The state dot's colors, like GitHub's.
fn standing_tone(standing: Standing) -> Tone {
    match standing {
        Standing::Ready => Tone::Good,
        Standing::Blocked => Tone::Bad,
        Standing::Merged => Tone::Done,
        Standing::Pending => Tone::Muted,
    }
}

pub fn spans(form: Form) -> Vec<Span<'static>> {
    form.into_iter()
        .map(|(text, tone)| Span::styled(text, style(tone)))
        .collect()
}

fn style(tone: Tone) -> Style {
    match tone {
        Tone::Muted => theme::muted_style(),
        Tone::Text => theme::text(theme::foreground()),
        Tone::Good => theme::text(theme::text_success()),
        Tone::Bad => theme::text(theme::text_error()),
        Tone::Done => theme::text(theme::text_accent()),
        Tone::Gauge => gauge(theme::gauge_fill()),
        Tone::GaugeWarn => gauge(theme::warning()),
        Tone::GaugeAlarm => gauge(theme::error()),
    }
}

fn gauge(fill: ratatui::style::Color) -> Style {
    theme::text(fill).bg(theme::gauge_track())
}
