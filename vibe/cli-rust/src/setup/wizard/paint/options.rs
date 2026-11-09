//! The shared option-list body of the auth-method and sign-in-target screens:
//! bordered cards with a badge, an "or" divider, a selection marker, and the
//! navigation hints.

use std::borrow::Cow;

use ratatui::layout::{Alignment, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders};
use ratatui::Frame;

use super::panel::{render, Hint, Row, Subtitle};
use crate::app::App;
use crate::hints;
use crate::setup::wizard::ValidationState;
use crate::ui::theme;

/// Esc goes back one screen everywhere except the welcome screen.
const OPTION_HINTS: &[hints::Hint] = &[hints::NAVIGATE, hints::SELECT, hints::BACK];

#[derive(Clone)]
pub(super) struct Opt {
    pub title: Cow<'static, str>,
    pub badge: &'static str,
    pub desc: &'static str,
}

/// `--setup`'s choice between Mistral AI and the active model's provider.
pub(super) fn provider_opts(provider: &str) -> Vec<Opt> {
    vec![
        Opt {
            title: Cow::Borrowed("Set up Mistral AI"),
            badge: "Recommended",
            desc: "Connectors, Mistral models and more. Sign in or paste a key.",
        },
        Opt {
            title: Cow::Owned(format!("Set up {provider}")),
            badge: "",
            desc: "Paste its API key to keep using your active model.",
        },
    ]
}

pub(super) const AUTH_OPTS: [Opt; 2] = [
    Opt {
        title: Cow::Borrowed("Launch browser"),
        badge: "Recommended",
        desc: "Sign in to Mistral AI Studio and finish setup automatically.",
    },
    Opt {
        title: Cow::Borrowed("Use an API key"),
        badge: "",
        desc: "Already have a key? Paste it manually instead.",
    },
];
pub(super) const TARGET_OPTS: [Opt; 2] = [
    Opt {
        title: Cow::Borrowed("Mistral AI"),
        badge: "Recommended",
        desc: "Sign in to Mistral AI Studio.",
    },
    Opt {
        title: Cow::Borrowed("Other"),
        badge: "",
        desc: "Sign in to a Mistral-compatible deployment on your own domain.",
    },
];

/// Draw a centered option list with title, subtitle, bordered cards, markers,
/// and an optional warning under the cards: the armed override confirm wins
/// over the shell-key notice. The panel only grows when one shows; without
/// it, both option screens share one rhythm.
#[allow(clippy::too_many_arguments)]
pub(super) fn draw(
    app: &mut App,
    wizard: &mut crate::setup::wizard::OnboardingState,
    f: &mut Frame,
    area: Rect,
    selected: usize,
    title: &str,
    subtitle: &str,
    opts: Cow<'static, [Opt]>,
    chat_frame: &str,
    warning: Option<&str>,
    shell_key_override: Option<&str>,
) {
    // The wrapped warning sits one blank row under the cards; the hint keeps
    // the two blank rows every panel screen uses after the last content row.
    let mut rows = vec![Row::Blank(2), Row::OptionCards { selected, opts }];
    if let Some(domain) = warning {
        rows.push(Row::Blank(1));
        rows.push(Row::Warning(wrapped_warning(domain, 68)));
    } else if let Some(env_var) = shell_key_override {
        rows.push(Row::Blank(1));
        rows.push(Row::Feedback(ValidationState::Warning(
            shell_override_notice(env_var),
        )));
    } else {
        rows.push(Row::Blank(2));
    }
    rows.push(Row::Hint(Hint::Keys(OPTION_HINTS)));
    render(
        app,
        wizard,
        f,
        area,
        chat_frame,
        title,
        Subtitle::Text(subtitle.to_owned()),
        &rows,
    );
}

/// "This replaces your configured custom domain (...). Press Enter again to
/// continue." wrapped to the panel's content width, one row per line, with
/// Enter highlighted in the error color wherever it lands.
pub(super) fn draw_warning(f: &mut Frame, x: u16, y: u16, lines: &[String]) {
    let style = Style::default().fg(theme::error());
    let key = Style::default()
        .fg(theme::error())
        .add_modifier(Modifier::BOLD);
    for (offset, line) in lines.iter().enumerate() {
        let row = y + offset as u16;
        let mut col = x;
        let mut rest = line.as_str();
        while let Some(at) = rest.find("Enter") {
            let (head, tail) = rest.split_at(at);
            f.buffer_mut().set_string(col, row, head, style);
            col += head.chars().count() as u16;
            f.buffer_mut().set_string(col, row, "Enter", key);
            col += 5;
            rest = &tail[5..];
        }
        f.buffer_mut().set_string(col, row, rest, style);
    }
}

pub(super) fn shell_override_notice(env_var: &str) -> String {
    format!(
        "{env_var} is set in your shell and overrides the key saved by setup. \
         Unset it (e.g. in your shell rc file) to use the new key."
    )
}

fn wrapped_warning(domain: &str, width: u16) -> Vec<String> {
    crate::utils::text::wrap_hard(
        &format!(
            "This replaces your configured custom domain ({domain}). \
             Press Enter again to continue."
        ),
        width as usize,
    )
}

/// The cards: one 4-row card per option with an "or" divider between them,
/// stacked inside the row's `area`.
pub(super) fn draw_option_cards(
    f: &mut Frame,
    area: Rect,
    panel_x: u16,
    selected: usize,
    opts: &[Opt],
) {
    for (index, option) in opts.iter().enumerate() {
        let card_y = area.y + index as u16 * 7;
        if index > 0 {
            f.buffer_mut()
                .set_string(area.x, card_y - 2, "or", theme::muted_style());
        }
        let selected = index == selected;
        if selected {
            f.buffer_mut()
                .set_string(panel_x, card_y + 1, ">", Style::default().fg(theme::ORANGE));
        }
        let card_area = Rect::new(area.x, card_y, area.width, 4);
        let mut block = Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme::fixed::ONBOARDING_CARD_BORDER));
        if !option.badge.is_empty() {
            block = block
                .title(format!(" {} ─", option.badge))
                .title_style(
                    Style::default()
                        .fg(theme::primary())
                        .add_modifier(Modifier::BOLD),
                )
                .title_alignment(Alignment::Right);
        }
        f.render_widget(block, card_area);
        // The badge title's trailing "─" spans the corner cell; repaint it
        // without bold so the corner matches the rest of the border.
        if !option.badge.is_empty() {
            f.buffer_mut().set_string(
                card_area.x + card_area.width.saturating_sub(2),
                card_area.y,
                "─",
                Style::default()
                    .fg(theme::fixed::ONBOARDING_CARD_BORDER)
                    .remove_modifier(Modifier::BOLD),
            );
        }
        if selected {
            let accent = Style::default().fg(theme::ORANGE);
            f.buffer_mut()
                .set_string(card_area.x, card_area.y + 1, "│", accent);
            f.buffer_mut()
                .set_string(card_area.x, card_area.y + 2, "│", accent);
        }
        f.buffer_mut().set_string(
            card_area.x + 2,
            card_area.y + 1,
            &option.title,
            Style::default()
                .fg(theme::foreground())
                .add_modifier(Modifier::BOLD),
        );
        f.buffer_mut().set_string(
            card_area.x + 2,
            card_area.y + 2,
            option.desc,
            theme::muted_style(),
        );
    }
}
