//! The shared onboarding panel chrome. Every screen that Python lays out with
//! `onboarding.tcss` (`.onboarding-panel` > `.onboarding-chat` + heading +
//! subtitle + body) renders through here, so the skeleton cannot drift:
//!
//! ```text
//! <petit chat>
//! <title>
//! <subtitle>
//! <body: the screen's rows, laid out by the walk below>
//! ```
//!
//! A screen declares its body as an ordered stack of [`Row`]s; the panel
//! assigns each row its line, derives the content height from the rows, and
//! paints them with the shared per-type painters. No screen holds row offsets
//! of its own.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::Frame;

use super::browser_sign_in;
use super::input;
use super::options;
use crate::app::App;
use crate::setup::wizard::{
    BrowserSignInState, InputCard, OnboardingInput, OnboardingState, SignInVariant, ValidationState,
};
use crate::ui::theme;

/// Heading color (Python `$mistral_orange_title`); not the selection orange.
pub(super) const TITLE_ORANGE: Color = Color::Rgb(0xff, 0x5a, 0x00);
/// Idle card border (Python `.onboarding-card` border).
pub(super) const CARD_BORDER: Color = Color::Rgb(0x30, 0x30, 0x40);

/// The chrome above the body: three cat rows, the title, the subtitle.
const CHROME_ROWS: u16 = 5;

/// Panel width for every panel screen (a deliberate divergence: Python's
/// `#api-key-panel` is 76, but the wizard keeps one width).
pub(super) const PANEL_WIDTH: u16 = 70;

/// One row of a panel body. Heights come from the row's content, so the
/// screens only decide what sits on the stack, in order.
pub(super) enum Row {
    /// `lines` blank rows.
    Blank(u16),
    /// A labeled input card (Python `.onboarding-card` input), three rows
    /// tall; clicking it focuses its input, like Textual's `Input`.
    InputCard {
        input: OnboardingInput,
        focused: bool,
        title: &'static str,
        placeholder: &'static str,
        secure: bool,
        card: InputCard,
    },
    /// The validation line under an input card; painted only when the state
    /// carries something to say ([`input::draw_validation_feedback`]).
    Feedback(ValidationState),
    /// One muted text line.
    Text(String),
    /// One external link line, underlined like the transcript's markdown
    /// links and registered with the mouse layer and the link hitmap.
    Link(String),
    /// The option-list cards with their selection markers and "or" divider
    /// (one four-row card per option).
    OptionCards {
        selected: usize,
        opts: &'static [options::Opt],
    },
    /// The three boxed browser sign-in step cards, stacked without gaps.
    Steps {
        step_idx: usize,
        state: BrowserSignInState,
    },
    /// The wrapped sign-in-target warning, Enter highlighted wherever it
    /// lands (Python `#sign-in-target-warning`).
    Warning(Vec<String>),
    /// The "copy this URL" help line under the step cards.
    UrlHelp,
    /// The wrapped sign-in URL reveal lines, one row per line.
    UrlLines(Vec<String>),
    /// The bottom hint line.
    Hint(Hint),
}

/// The hint line's copy, one flavor per screen family.
pub(super) enum Hint {
    /// `Press <key><suffix>`, the key highlighted.
    Press {
        key: &'static str,
        suffix: &'static str,
    },
    /// The option screens' navigation hints.
    Options { escape_action: String },
    /// The browser sign-in's centered variant hint.
    Browser(SignInVariant),
}

impl Row {
    /// The row's height in lines at the given content width: the message
    /// rows (feedback, text) wrap to it like Python's Textual labels, the
    /// fixed-geometry rows ignore it.
    fn height(&self, width: u16) -> u16 {
        match self {
            Row::Blank(lines) => *lines,
            Row::InputCard { .. } => 3,
            Row::Feedback(ValidationState::Invalid(msg) | ValidationState::Warning(msg)) => {
                crate::utils::text::wrap_hard(msg, width as usize).len() as u16
            }
            Row::Feedback(_) => 1,
            Row::Text(text) => crate::utils::text::wrap_hard(text, width as usize).len() as u16,
            Row::Link(_) | Row::UrlHelp | Row::Hint(_) => 1,
            // One four-row card per option, with a blank row, the "or"
            // divider, and another blank between cards.
            Row::OptionCards { opts, .. } => opts.len() as u16 * 7 - 3,
            Row::Steps { .. } => browser_sign_in::STEPS_HEIGHT,
            Row::Warning(lines) | Row::UrlLines(lines) => lines.len() as u16,
        }
    }
}

/// The subtitle line under the title; the two styles Python uses.
pub(super) enum Subtitle {
    /// Bold foreground (Python `#auth-method-subtitle` and friends).
    Text(String),
    /// Muted (Python `#browser-sign-in-subtitle`).
    Muted(String),
}

/// Draw the shared chrome (cat, title, subtitle), then the screen's
/// rows: the walk assigns each row its line, and the height the rows add
/// up to centers the panel vertically in the frame.
#[allow(clippy::too_many_arguments)]
pub(super) fn render(
    app: &mut App,
    wizard: &mut OnboardingState,
    f: &mut Frame,
    area: Rect,
    chat_frame: &str,
    title: &str,
    subtitle: Subtitle,
    rows: &[Row],
) {
    let panel_w = PANEL_WIDTH.min(area.width);
    let panel_x = area.x + (area.width.saturating_sub(panel_w)) / 2;
    let x = panel_x + 2;
    let width = panel_w.saturating_sub(2);
    let content_height = CHROME_ROWS + rows.iter().map(|row| row.height(width)).sum::<u16>();
    let bottom = area.y + area.height;
    let y = area.y + area.height.saturating_sub(content_height) / 2;
    // A short terminal shows the panel's top slice, never a panic: a
    // cell at row r is inside only while r < bottom, and every painter
    // below assumes the full height it was handed.
    if y + 3 <= bottom {
        draw_petit_chat(f, x, y, chat_frame);
    }
    if y + 4 <= bottom {
        orange_title(f, x, y + 3, title);
    }
    let (text, style) = match subtitle {
        Subtitle::Text(text) => (
            text,
            Style::default()
                .fg(theme::foreground())
                .add_modifier(Modifier::BOLD),
        ),
        Subtitle::Muted(text) => (text, theme::muted_style()),
    };
    if y + 5 <= bottom {
        f.buffer_mut().set_string(x, y + 4, text, style);
    }
    let mut input_rows = Vec::new();
    let mut row_y = y + CHROME_ROWS;
    for row in rows {
        let height = row.height(width);
        // Stop drawing once the content reaches the bottom of the
        // screen: ratatui panics on a y outside the buffer.
        if row_y.saturating_add(height) > bottom {
            break;
        }
        let rect = Rect::new(x, row_y, width, height);
        match row {
            Row::Blank(_) => {}
            Row::InputCard {
                input,
                focused,
                title: card_title,
                placeholder,
                secure,
                card,
            } => {
                input_rows.push((rect, *card));
                crate::mouse::register_region(
                    app,
                    rect,
                    crate::mouse::MouseTarget::OnboardingInputs,
                );
                input::draw_input_card(f, rect, input, *focused, card_title, placeholder, *secure);
            }
            Row::Feedback(validation) => {
                input::draw_validation_feedback(f, x, row_y, width, validation);
            }
            Row::Text(text) => {
                for (offset, line) in crate::utils::text::wrap_hard(text, width as usize)
                    .iter()
                    .enumerate()
                {
                    f.buffer_mut()
                        .set_string(x, row_y + offset as u16, line, theme::muted_style());
                }
            }
            Row::Link(url) => draw_link(app, f, x, row_y, width, url),
            Row::OptionCards { selected, opts } => {
                options::draw_option_cards(f, rect, panel_x, *selected, opts);
            }
            Row::Steps { step_idx, state } => {
                browser_sign_in::draw_step_cards(f, rect, panel_x, *step_idx, state);
            }
            Row::Warning(lines) => options::draw_warning(f, x, row_y, lines),
            Row::UrlHelp => browser_sign_in::draw_url_help(f, x, row_y),
            Row::UrlLines(lines) => {
                for (offset, line) in lines.iter().enumerate() {
                    f.buffer_mut()
                        .set_string(x, row_y + offset as u16, line, theme::muted_style());
                }
            }
            Row::Hint(hint) => match hint {
                Hint::Press { key, suffix } => input::draw_press_hint(f, x, row_y, key, suffix),
                Hint::Options { escape_action } => {
                    options::draw_hints(f, x, row_y, escape_action);
                }
                Hint::Browser(variant) => browser_sign_in::draw_hint(f, rect, *variant),
            },
        }
        row_y += height;
    }
    // Only screens with input cards own the click-to-focus rows; the
    // others keep whatever the last input screen registered.
    if !input_rows.is_empty() {
        wizard.input_rows = input_rows;
    }
}

/// One external link line, like the transcript's markdown links: the shared
/// link style and hover paint, and the shared hitmap the mouse layer opens
/// on a plain click.
fn draw_link(app: &mut App, f: &mut Frame, x: u16, y: u16, width: u16, url: &str) {
    let style = Style::default()
        .fg(theme::md_link())
        .add_modifier(Modifier::UNDERLINED);
    f.buffer_mut().set_string(x, y, url, style);
    crate::mouse::register_region(
        app,
        Rect::new(x, y, url.len() as u16, 1),
        crate::mouse::MouseTarget::OnboardingLinks,
    );
    let mut linked = crate::ui::markdown::LinkedLines::default();
    let link = linked.link(url.to_string(), crate::ui::markdown::LinkKind::External);
    let row: Vec<crate::ui::markdown::Sc> = url.chars().map(|c| (c, style, Some(link))).collect();
    linked.push_row(Vec::new(), &row);
    let links = crate::ui::markdown::screen_links(&linked, true, Rect::new(x, y, width, 1), 0);
    if let Some(position) = app.view.mouse_position {
        for link in &links {
            if link.contains(position) {
                link.paint_hover(f.buffer_mut());
            }
        }
    }
    app.view.link_hitmap.extend(links);
}

/// The shared heading style: orange, bold.
fn orange_title(f: &mut Frame, x: u16, y: u16, text: &str) {
    f.buffer_mut().set_string(
        x,
        y,
        text,
        Style::default()
            .fg(TITLE_ORANGE)
            .add_modifier(Modifier::BOLD),
    );
}

/// The current cat frame, as braille rows (Python `PetitChat`).
fn draw_petit_chat(f: &mut Frame, x: u16, y: u16, frame: &str) {
    for (offset, line) in frame.lines().enumerate() {
        f.buffer_mut().set_string(
            x,
            y + offset as u16,
            line,
            Style::default().fg(theme::foreground()),
        );
    }
}
