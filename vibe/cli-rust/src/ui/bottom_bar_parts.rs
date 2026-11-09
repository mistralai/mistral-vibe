//! The bottom bar's styled parts: the PR/MR link input and each tone's style.

use ratatui::style::{Modifier, Style};
use ratatui::text::Span;

use super::footer_fit::{Form, LinkInput, Tone};
use super::theme;
use crate::app::App;
use crate::artifacts::Standing;

/// The PR/MR link, with `enable_pr_monitoring`: the one active artifact, else a count.
pub fn link_input(app: &App) -> Option<LinkInput> {
    if !app.artifacts_modal.enabled {
        return None;
    }
    let shown = crate::artifacts::bar_artifacts(app);
    let unseen = shown.iter().any(|a| crate::artifacts::is_unseen(app, a));
    let link = match shown.as_slice() {
        [] => return None,
        [artifact] => LinkInput {
            label: crate::artifacts::label(artifact),
            short: crate::artifacts::short_label(artifact),
            status: crate::artifacts::status_label(artifact),
            dot: Some(standing_tone(crate::artifacts::standing(artifact))),
            unseen,
        },
        artifacts => {
            let label = crate::artifacts::count_label(artifacts);
            LinkInput {
                short: label.clone(),
                label,
                status: None,
                dot: None,
                unseen,
            }
        }
    };
    Some(link)
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
        Tone::Unseen => theme::text(theme::primary()).add_modifier(Modifier::BOLD),
        Tone::Good => theme::text(theme::text_success()),
        Tone::Bad => theme::text(theme::text_error()),
        Tone::Done => theme::text(theme::merged()),
        Tone::Gauge => gauge(theme::gauge_fill()),
        Tone::GaugeWarn => gauge(theme::warning()),
        Tone::GaugeAlarm => gauge(theme::error()),
    }
}

fn gauge(fill: ratatui::style::Color) -> Style {
    theme::text(fill).bg(theme::gauge_track())
}
