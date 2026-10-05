//! The browser sign-in step tracker (Python `BrowserSignInScreen`): three
//! boxed step cards with the active one marked, the URL copy help, and the
//! centered wizard hints.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use ratatui::Frame;

use super::panel::{render, Hint, Row, Subtitle, CARD_BORDER};
use super::welcome::GRADIENT;
use crate::app::App;
use crate::setup::wizard::{BrowserSignInState, OnboardingState, SignInStep, SignInVariant};
use crate::ui::theme;

/// The step titles and their two detail lines (Python `STEP_DESCRIPTIONS`).
const STEP_TITLES: [&str; 3] = ["Open browser", "Complete sign-in", "Finished setup"];
const STEP_PENDING_DETAILS: [&str; 3] = [
    "Your browser should open automatically",
    "Waiting for authentication...",
    "Vibe will start automatically",
];
const STEP_DONE_DETAILS: [&str; 3] = ["Browser opened", "Sign-in confirmed.", "Setup complete."];
/// The step cards' total height: three boxed cards, four rows each.
pub(super) const STEPS_HEIGHT: u16 = STEP_TITLES.len() as u16 * 4;
/// Python `WAITING_FOR_AUTHENTICATION_MESSAGE`, painted as a gradient.
const WAITING_FOR_AUTHENTICATION: &str = "Waiting for authentication...";
/// Python `SIGN_IN_URL_FALLBACK_PREFIX` and the capitalized `NATIVE_COPY_HINT`
/// that follows the raw URL once the copy key revealed it.
const URL_FALLBACK_PREFIX: &str =
    "If copying to the clipboard was not successful, copy the following URL:";
const NATIVE_COPY_HINT: &str = "If paste fails, hold Shift (Option in iTerm2, Fn in Terminal.app) while selecting for native copy";

/// The URL row is visible once the copy help delay passed (Python
/// `SIGN_IN_URL_HELP_DELAY_SECONDS`), or right after a copy reveals it.
fn url_help_visible(state: &BrowserSignInState) -> bool {
    state.variant != SignInVariant::Success
        && state.sign_in_url.is_some()
        && (state.show_url_help
            || state
                .sign_in_started_at
                .is_some_and(|at| at.elapsed() >= std::time::Duration::from_secs(4)))
}

/// Python `_build_url_text`'s reveal branch: the fallback prefix, the raw
/// URL, and the native-copy hint, wrapped to the URL widget's content width.
fn reveal_lines(state: &BrowserSignInState) -> Vec<String> {
    let Some(url) = state.sign_in_url.as_deref() else {
        return Vec::new();
    };
    crate::utils::text::wrap_hard(
        &format!("{URL_FALLBACK_PREFIX} {url}. {NATIVE_COPY_HINT}."),
        68,
    )
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
    // Python keeps the URL Static mounted, so its row is always reserved:
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

/// The three boxed step cards, stacked without gaps inside the row's `area`.
pub(super) fn draw_step_cards(
    f: &mut Frame,
    area: Rect,
    panel_x: u16,
    step_idx: usize,
    state: &BrowserSignInState,
) {
    for index in 0..3 {
        draw_step_card(f, area, panel_x, index, step_idx, state);
    }
}

/// One step card (Python `.browser-sign-in-step`): a boxed 4-row card whose
/// title and detail restate the step, recolored by its done/active/idle class.
fn draw_step_card(
    f: &mut Frame,
    area: Rect,
    panel_x: u16,
    index: usize,
    step_idx: usize,
    state: &BrowserSignInState,
) {
    let card_y = area.y + index as u16 * 4;
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(CARD_BORDER)),
        Rect::new(area.x, card_y, area.width, 4),
    );
    // The card's border, padding, and title padding leave the text at +4.
    let text_x = area.x + 4;
    let title = Style::default()
        .fg(theme::foreground())
        .add_modifier(Modifier::BOLD);
    let detail = theme::muted_style();
    if index < step_idx {
        // A finished step keeps its border but shows its done detail, with a
        // success left edge (Python `.browser-sign-in-step.done`).
        let success = Style::default().fg(theme::success());
        f.buffer_mut().set_string(area.x, card_y + 1, "│", success);
        f.buffer_mut().set_string(area.x, card_y + 2, "│", success);
        f.buffer_mut()
            .set_string(text_x, card_y + 1, STEP_TITLES[index], title);
        f.buffer_mut()
            .set_string(text_x, card_y + 2, STEP_DONE_DETAILS[index], detail);
    } else if index == step_idx {
        f.buffer_mut()
            .set_string(panel_x, card_y + 1, ">", Style::default().fg(theme::ORANGE));
        f.buffer_mut()
            .set_string(text_x, card_y + 1, STEP_TITLES[index], title);
        draw_active_detail(f, text_x, card_y + 2, area.width.saturating_sub(4), state);
    } else {
        f.buffer_mut().set_string(
            text_x,
            card_y + 1,
            STEP_TITLES[index],
            detail.add_modifier(Modifier::BOLD),
        );
        f.buffer_mut()
            .set_string(text_x, card_y + 2, STEP_PENDING_DETAILS[index], detail);
    }
}

/// The active step's detail line: the gradient while waiting on the confirm
/// step, the status message colored by the variant otherwise.
fn draw_active_detail(f: &mut Frame, x: u16, y: u16, width: u16, state: &BrowserSignInState) {
    if state.variant == SignInVariant::Pending && state.step == SignInStep::Confirm {
        // Python animates the gradient offset; the wizard replays pin it at
        // zero so captures stay deterministic.
        let spans: Vec<Span> = WAITING_FOR_AUTHENTICATION
            .chars()
            .enumerate()
            .map(|(offset, ch)| {
                Span::styled(
                    ch.to_string(),
                    Style::default()
                        .fg(GRADIENT[offset % GRADIENT.len()])
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
    f.buffer_mut().set_string(
        x,
        y,
        state.message.as_str(),
        Style::default().fg(color).add_modifier(Modifier::BOLD),
    );
}

/// Python `_build_url_text`'s help line: the prefix, the clickable copy
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

/// The bottom hint, centered in the row's `area` (Python
/// `#browser-sign-in-hint`; the Esc wording deliberately diverges: Esc goes
/// back to the sign-in target, it does not cancel the wizard).
pub(super) fn draw_hint(f: &mut Frame, area: Rect, variant: SignInVariant) {
    let text = theme::muted_style();
    let key = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    let segments: &[(&str, Style)] = match variant {
        SignInVariant::Success => &[("Finishing setup...", text)],
        SignInVariant::Error => &[
            ("Press ", text),
            ("r", key),
            (" to retry - Press ", text),
            ("m", key),
            (" to enter API key manually - ", text),
            ("Esc", key),
            (" to go back", text),
        ],
        SignInVariant::Pending => &[
            ("Press ", text),
            ("m", key),
            (" to enter API key manually - ", text),
            ("Esc", key),
            (" to go back", text),
        ],
    };
    let total: u16 = segments
        .iter()
        .map(|(segment, _)| segment.chars().count() as u16)
        .sum();
    let x = area.x + area.width.saturating_sub(total) / 2;
    set_segments(f, x, area.y, segments);
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
