//! The composer's top-border title: the active model, its thinking level muted, then the agent and approval policy.

use ratatui::layout::Rect;
use ratatui::style::Color;
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::theme;
use crate::agents;
use crate::app::App;
use crate::server::AgentSafety;
use crate::utils::text::ellipsize;

/// Between the model, the agent and the policy.
const SEP: &str = " · ";
/// The model's name shrinks to this many cells before it hides.
const MODEL_FLOOR: usize = 12;

/// The policy label's color per safety; the borders stay neutral.
fn safety_color(safety: AgentSafety) -> Color {
    match safety {
        AgentSafety::Safe => theme::success(),
        AgentSafety::Neutral => theme::muted(),
        AgentSafety::Destructive | AgentSafety::Smart => theme::warning(),
        AgentSafety::Yolo => theme::error(),
    }
}

/// The title at `width` cells, right-aligned on the top border, and the model's cells within it.
pub struct Title {
    pub line: Line<'static>,
    /// The model name's offset and width from the title's left edge; zero wide when hidden.
    pub model: (u16, u16),
}

/// ` mistral-medium-3.5[high] · lean · accept edits ─`, narrowing the model first: no thinking level, an elided name, then none.
pub fn title(app: &App, width: u16) -> Title {
    let policy = agents::displayed(app);
    let bypass = agents::bypass_tool_permissions(app);
    let policy_name = agents::indicator_policy_name(policy, bypass);
    let mode = match agents::indicator_agent_name(app) {
        Some(agent) => format!("{agent}{SEP}{policy_name}"),
        None => policy_name,
    };
    let mode_style = theme::text(safety_color(agents::indicator_safety(policy, bypass)));
    let border = theme::border_style();
    let model = app.session.startup_config.active_model.as_str();
    let (name, thinking) = model.find('[').map_or((model, ""), |at| model.split_at(at));
    // The border keeps a corner of its own on each side of the title.
    let room = (width as usize).saturating_sub(2);
    let sep = if mode.is_empty() { "" } else { SEP };
    let fixed = 1 + mode.width() + 1;
    let forms = [
        (name.to_owned(), thinking),
        (name.to_owned(), ""),
        (ellipsize(name, MODEL_FLOOR), ""),
    ];
    let chosen = forms
        .into_iter()
        .filter(|(name, _)| !name.is_empty())
        .find(|(name, thinking)| fixed + name.width() + thinking.width() + sep.width() <= room);
    let mut spans = vec![Span::raw(" ")];
    let mut model_cells = (1, 0);
    if let Some((name, thinking)) = chosen {
        model_cells.1 = (name.width() + thinking.width()) as u16;
        spans.push(Span::styled(name, theme::text(theme::foreground())));
        if !thinking.is_empty() {
            spans.push(Span::styled(thinking.to_owned(), theme::muted_style()));
        }
        if mode.is_empty() {
            spans.push(Span::raw(" "));
        }
        spans.push(Span::styled(sep, theme::muted_style()));
    }
    if !mode.is_empty() {
        spans.push(Span::styled(format!("{mode} "), mode_style));
    }
    spans.push(Span::styled("─", border));
    Title {
        line: Line::from(spans),
        model: model_cells,
    }
}

/// The bottom-border hint shown while Ctrl+S holds a stashed draft and can restore it.
pub fn stash_hint(app: &App) -> Line<'static> {
    if app.chat_input.stash.is_none() || !crate::composer_stash::key_reaches_stash(app) {
        return Line::default();
    }
    let hint = (
        crate::hints::key::CTRL_S,
        crate::hints::action::RESTORE_STASH,
    );
    let mut spans = vec![Span::raw(" ")];
    spans.extend(super::hint_line::spans(&[hint]));
    spans.push(Span::raw(" "));
    spans.push(Span::styled("─", theme::border_style()));
    Line::from(spans).right_aligned()
}

/// Publish where the title painted the model in the composer at `area`, for `/model` clicks.
pub fn place_model(app: &mut App, area: Rect, title: &Title) {
    let x = area.right().saturating_sub(title.line.width() as u16) + title.model.0;
    app.view.model_area = Rect {
        x,
        y: area.y,
        width: title.model.1,
        height: 1,
    };
}

/// Whether `at` is on the composer's model name while a click there may open `/model`, which needs an idle session like the typed command.
pub fn model_at(app: &App, at: (u16, u16)) -> bool {
    app.focus() == crate::focus::Focus::Composer
        && app.view.model_area.contains(at.into())
        && crate::commands::submission::accepts_idle_command(app)
}
