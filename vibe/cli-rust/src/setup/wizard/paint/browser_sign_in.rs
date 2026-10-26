//! The browser sign-in step tracker: three
//! boxed step cards with the active one marked, the URL copy help, and the
//! centered wizard hints.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use ratatui::Frame;

use super::panel::{render, Hint, Row, Subtitle};
use crate::app::App;
use crate::hints::{self, action};
use crate::setup::wizard::sign_in::{
    max_detail_lines, step_detail_lines, BrowserSignInState, SignInStep, SignInVariant, STEP_TITLES,
};
use crate::setup::wizard::OnboardingState;
use crate::ui::{hint_line, theme};
use unicode_width::UnicodeWidthStr;

/// The sign-in URL fallback prefix that precedes the raw URL once the copy key revealed it.
const URL_FALLBACK_PREFIX: &str =
    "If copying to the clipboard was not successful, copy the following URL:";

/// The URL row is visible once the copy help delay passed, or right after a
/// copy reveals it.
fn url_help_visible(state: &BrowserSignInState) -> bool {
    state.variant != SignInVariant::Success
        && state.sign_in_url.is_some()
        && (state.show_url_help
            || state
                .sign_in_started_at
                .is_some_and(|at| at.elapsed() >= std::time::Duration::from_secs(4)))
}

/// The revealed URL text: the fallback prefix, the raw
/// URL, and the native-copy hint, wrapped to the URL widget's content width.
fn reveal_lines(state: &BrowserSignInState) -> Vec<String> {
    let Some(url) = state.sign_in_url.as_deref() else {
        return Vec::new();
    };
    let mut hint = crate::clipboard::unverified_hint().to_owned();
    if let Some(first) = hint.get_mut(..1) {
        first.make_ascii_uppercase();
    }
    crate::utils::text::wrap_hard(&format!("{URL_FALLBACK_PREFIX} {url}. {hint}."), 68)
}

/// Draw the browser sign-in step tracker screen.
pub(super) fn draw(
    app: &mut App,
    wizard: &mut OnboardingState,
    f: &mut Frame,
    area: Rect,
    chat_frame: &str,
) {
    let state = wizard.browser_sign_in.clone();
    let url_visible = url_help_visible(&state);
    let revealed = url_visible && state.reveal_sign_in_url;
    let reveal = revealed.then(|| reveal_lines(&state));
    let step_idx = match state.step {
        SignInStep::Open => 0,
        SignInStep::Confirm => 1,
        SignInStep::Finish => 2,
    };
    // The URL row is always reserved:
    // one blank row when there is nothing to say, the help line once the
    // delay passed, and the wrapped fallback text after a copy. The hint
    // keeps the two blank rows every panel screen uses before it.
    let mut rows = vec![
        Row::Blank(1),
        Row::Steps {
            step_idx,
            state: state.clone(),
        },
        Row::Blank(1),
    ];
    match &reveal {
        Some(lines) => rows.push(Row::UrlLines(lines.clone())),
        None if url_visible => rows.push(Row::UrlHelp),
        None => rows.push(Row::Blank(1)),
    }
    rows.push(Row::Blank(2));
    rows.push(Row::Hint(Hint::Browser(state.variant)));
    render(
        app,
        wizard,
        f,
        area,
        chat_frame,
        "Launch browser",
        Subtitle::Muted("Your browser should open automatically".into()),
        &rows,
    );
}

/// The three boxed step cards, stacked without gaps inside the row's `area`;
/// the active card grows by its wrapped detail lines, clamped so the cards
/// fit the `budget` rows the panel gave the stack.
pub(super) fn draw_step_cards(
    f: &mut Frame,
    area: Rect,
    panel_x: u16,
    step_idx: usize,
    budget: u16,
    state: &BrowserSignInState,
) {
    let max_lines = max_detail_lines(budget);
    let mut card_y = area.y;
    for index in 0..3 {
        let lines = step_detail_lines(state, step_idx, index, area.width, max_lines);
        let height = lines.len() as u16 + 3;
        draw_step_card(
            f,
            Rect::new(area.x, card_y, area.width, height),
            panel_x,
            index,
            step_idx,
            &lines,
            state,
        );
        card_y += height;
    }
}

/// One step card: a boxed card whose title and detail restate the step,
/// recolored by its done/active/idle class.
fn draw_step_card(
    f: &mut Frame,
    card: Rect,
    panel_x: u16,
    index: usize,
    step_idx: usize,
    lines: &[String],
    state: &BrowserSignInState,
) {
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme::fixed::ONBOARDING_CARD_BORDER)),
        card,
    );
    // The card's border, padding, and title padding leave the text at +4.
    let text_x = card.x + 4;
    let title = Style::default()
        .fg(theme::foreground())
        .add_modifier(Modifier::BOLD);
    let detail = theme::muted_style();
    if index < step_idx {
        // A finished step keeps its border but shows its done detail, with a
        // success left edge.
        let success = Style::default().fg(theme::success());
        f.buffer_mut().set_string(card.x, card.y + 1, "│", success);
        f.buffer_mut().set_string(card.x, card.y + 2, "│", success);
        f.buffer_mut()
            .set_string(text_x, card.y + 1, STEP_TITLES[index], title);
        f.buffer_mut()
            .set_string(text_x, card.y + 2, &lines[0], detail);
    } else if index == step_idx {
        f.buffer_mut()
            .set_string(panel_x, card.y + 1, ">", Style::default().fg(theme::ORANGE));
        f.buffer_mut()
            .set_string(text_x, card.y + 1, STEP_TITLES[index], title);
        draw_active_detail(
            f,
            text_x,
            card.y + 2,
            card.width.saturating_sub(4),
            lines,
            state,
        );
    } else {
        f.buffer_mut().set_string(
            text_x,
            card.y + 1,
            STEP_TITLES[index],
            detail.add_modifier(Modifier::BOLD),
        );
        f.buffer_mut()
            .set_string(text_x, card.y + 2, &lines[0], detail);
    }
}

/// The active step's detail lines: the gradient while waiting on the confirm
/// step, the wrapped status message colored by the variant otherwise.
fn draw_active_detail(
    f: &mut Frame,
    x: u16,
    y: u16,
    width: u16,
    lines: &[String],
    state: &BrowserSignInState,
) {
    if state.variant == SignInVariant::Pending && state.step == SignInStep::Confirm {
        // The gradient offset animates; the wizard replays pin it at zero so
        // captures stay deterministic.
        let spans: Vec<Span> = crate::setup::wizard::sign_in::WAITING_FOR_AUTHENTICATION
            .chars()
            .enumerate()
            .map(|(offset, ch)| {
                Span::styled(
                    ch.to_string(),
                    Style::default()
                        .fg(theme::fixed::WELCOME_GRADIENT
                            [offset % theme::fixed::WELCOME_GRADIENT.len()])
                        .add_modifier(Modifier::BOLD),
                )
            })
            .collect();
        f.render_widget(Paragraph::new(Line::from(spans)), Rect::new(x, y, width, 1));
        return;
    }
    let color = match state.variant {
        SignInVariant::Pending => theme::ORANGE,
        SignInVariant::Error => theme::error(),
        SignInVariant::Success => theme::success(),
    };
    let style = Style::default().fg(color).add_modifier(Modifier::BOLD);
    for (offset, line) in lines.iter().enumerate() {
        f.buffer_mut().set_string(x, y + offset as u16, line, style);
    }
}

/// The URL help line: the prefix, the clickable copy
/// label, and the "(press c)" suffix.
pub(super) fn draw_url_help(f: &mut Frame, x: u16, y: u16) {
    let text = theme::muted_style();
    let key = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    set_segments(
        f,
        x,
        y,
        &[
            ("If your browser did not open, ", text),
            ("copy this URL", theme::dim(theme::primary())),
            (" (press ", text),
            ("c", key),
            (").", text),
        ],
    );
}

/// The bottom hint, centered in the row's `area` (Esc goes back to the
/// sign-in target, it does not cancel the wizard).
pub(super) fn draw_hint(f: &mut Frame, area: Rect, variant: SignInVariant) {
    const API_KEY: hints::Hint = ("m", action::API_KEY);
    let keys: &[hints::Hint] = match variant {
        SignInVariant::Success => {
            let text = "Finishing setup...";
            let x = area.x + area.width.saturating_sub(text.width() as u16) / 2;
            f.buffer_mut()
                .set_string(x, area.y, text, theme::muted_style());
            return;
        }
        SignInVariant::Error => &[("r", action::RETRY), API_KEY, hints::BACK],
        SignInVariant::Pending => &[API_KEY, hints::BACK],
    };
    let x = area.x + area.width.saturating_sub(hints::width(keys)) / 2;
    hint_line::draw_clipped(f, x, area.y, area.right().saturating_sub(x), keys);
}

/// Write styled segments left to right on one row, returning the end column.
fn set_segments(f: &mut Frame, x: u16, y: u16, segments: &[(&str, Style)]) -> u16 {
    let mut col = x;
    for (segment, style) in segments {
        f.buffer_mut().set_string(col, y, *segment, *style);
        col += segment.chars().count() as u16;
    }
    col
}
