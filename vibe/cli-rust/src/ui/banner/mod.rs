//! Welcome banner: the animated braille cat beside an info block.

pub mod petit_chat;

use std::time::Instant;

use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use super::markdown::LinkedLines;
use super::theme;
use crate::selection::Fold;
use crate::utils::startup_cache::StartupConfig;
use petit_chat::{CatVariant, PetitChat};

/// Columns before the info block, the same for every cat variant so the text never moves.
const CAT_COLS: usize = 14;

/// The welcome banner: an animated cat beside a static info block.
#[derive(Default)]
pub struct Banner {
    chat: PetitChat,
    /// Plan label appended to the model line, from `account/read`.
    plan_title: Option<String>,
    /// Greeting mounted under the banner, from `identity/read`.
    greeting: Option<String>,
}

impl Banner {
    /// Advance the cat animation, returning whether its visible dots changed.
    pub fn tick(&mut self, now: Instant) -> bool {
        self.chat.tick(now)
    }

    /// Apply the post-ready account and identity reads.
    pub fn set_account(&mut self, plan_title: Option<String>, greeting: Option<String>) {
        self.plan_title = plan_title;
        self.greeting = greeting;
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

    /// The full banner: the info block beside the cat, or under it when `width` is too narrow.
    pub fn view(&self, config: &StartupConfig, width: u16) -> LinkedLines {
        // `#banner-container` `padding: 1 1 0 0`: one blank row above the cat.
        let mut lines = LinkedLines::default();
        lines.push_gap();

        let meta = Style::default().fg(theme::foreground());
        let brand = Style::default()
            .fg(theme::ORANGE)
            .add_modifier(Modifier::BOLD);
        let cmd = Style::default().fg(theme::secondary());

        let model = [
            Some(config.active_model.as_str()),
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
            .map(|row| format!("{row:<width$}", width = CAT_COLS));
        if info
            .iter()
            .any(|line| CAT_COLS + line.width() > usize::from(width))
        {
            lines.extend(cat.map(Line::from));
            lines.push_gap();
            lines.extend(info);
        } else {
            // The cat is indentation, so a copy keeps only the info text.
            for (row, info) in cat.zip(info) {
                let mut line = Line::from(row);
                line.spans.extend(info.spans);
                lines.push_folded(line, Some(Fold::indented(CAT_COLS as u16)));
            }
        }
        // `.greeting-message` `margin-top: 1`: one blank row above the greeting.
        if let Some(greeting) = &self.greeting {
            lines.push_gap();
            lines.push(Line::from(Span::styled(greeting.clone(), meta)));
        }
        lines
    }
}
