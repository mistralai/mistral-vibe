//! Welcome banner: the animated braille cat beside an info block.

pub mod petit_chat;

#[cfg(test)]
mod tests;

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use super::markdown::LinkedLines;
use super::theme;
use crate::selection::Fold;
use crate::utils::startup_cache::StartupConfig;
use petit_chat::{CatVariant, PetitChat};

/// Columns before the info block, the same for every cat variant so the text never moves.
const CAT_COLS: usize = 14;
/// The first banner-block row of the cat, under the gap row above it.
const CAT_FIRST_ROW: u16 = 1;
/// The cat's screen rows: its 12 dot rows pack four-per-glyph into braille rows.
const CAT_ROW_COUNT: u16 = 3;

/// The welcome banner: an animated cat beside a static info block.
#[derive(Default)]
pub struct Banner {
    chat: PetitChat,
    /// Plan label appended to the model line, from `account/read`.
    plan_title: Option<String>,
}

impl Banner {
    /// Advance the cat animation, returning whether its visible dots changed.
    pub fn tick(&mut self) -> bool {
        self.chat.tick()
    }

    /// Replay the cat's animation round, e.g. after a click on it.
    pub fn restart(&mut self) {
        self.chat.restart();
    }

    /// Apply the post-ready account read.
    pub fn set_account(&mut self, plan_title: Option<String>) {
        self.plan_title = plan_title;
    }

    /// Switch the cat variant based on the active model name.
    /// "ml4" or "Mistral Large" → LeChonk, anything else → LeChat.
    pub fn sync_model_variant(&mut self, config: &StartupConfig) {
        let variant = CatVariant::from_model(&config.active_model_display_name);
        self.chat.set_variant(variant);
    }

    /// The current cat frame as a braille grid, for screens that reuse it.
    pub fn chat_frame(&self) -> String {
        self.chat.render()
    }

    /// The full banner: the info block beside the cat, or under it when `width` is too narrow. `model` replaces the session's.
    pub fn view(&self, config: &StartupConfig, model: Option<&str>, width: u16) -> LinkedLines {
        // `#banner-container` `padding: 1 1 0 0`: one blank row above the cat.
        let mut lines = LinkedLines::default();
        lines.push_gap();

        let meta = Style::default().fg(theme::foreground());
        let brand = Style::default()
            .fg(theme::ORANGE)
            .add_modifier(Modifier::BOLD);
        let cmd = Style::default().fg(theme::secondary());

        let model = [
            Some(model.unwrap_or(&config.active_model)),
            self.plan_title.as_deref(),
        ]
        .into_iter()
        .flatten()
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" · ");
        let info = [
            Line::from(vec![
                Span::styled("Mistral Vibe", brand),
                Span::styled(format!(" v{}", config.server_version), meta),
            ]),
            Line::from(Span::styled(model, meta)),
            Line::from(vec![
                Span::styled("Type ", meta),
                Span::styled("/help", cmd),
                Span::styled(" for more information", meta),
            ]),
        ];
        let grid = self.chat.render();
        let cat = grid
            .lines()
            .map(|row| Line::from(Span::styled(format!("{row:<CAT_COLS$}"), meta)));
        if info
            .iter()
            .any(|line| CAT_COLS + line.width() > usize::from(width))
        {
            lines.extend(cat);
            lines.push_gap();
            lines.extend(info);
        } else {
            // The cat is indentation, so a copy keeps only the info text.
            for (mut line, info) in cat.zip(info) {
                line.spans.extend(info.spans);
                lines.push_folded(line, Some(Fold::indented(CAT_COLS as u16)));
            }
        }
        lines
    }
}

/// The cat's click area within a painted banner block: `CAT_COLS` wide, its rows
/// under the block's leading gap, clipped to the painted like the block itself.
pub(crate) fn cat_click_area(painted: Rect, scrolled: u16) -> Option<Rect> {
    let first = scrolled.max(CAT_FIRST_ROW);
    let last = scrolled
        .saturating_add(painted.height)
        .min(CAT_FIRST_ROW + CAT_ROW_COUNT);
    // A fully clipped cat (scrolled past, or an empty block) has no click area.
    let height = last.checked_sub(first).filter(|height| *height > 0)?;
    let y = painted.y + first - scrolled;
    let width = (CAT_COLS as u16).min(painted.width);
    Some(Rect::new(painted.x, y, width, height))
}
