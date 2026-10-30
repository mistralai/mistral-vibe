//! The manual update instructions printed instead of running the upgrade.

use ratatui::style::{Modifier, Style};

use super::{color_span, UpdatePromptState};
use crate::ui::theme;
use crate::update_notifier::gateway::UpdateSource;

/// The upgrade command for the install's own manager; uv is the documented install.
pub fn manual_update_command(source: UpdateSource) -> &'static str {
    match source {
        UpdateSource::Brew => "brew upgrade mistral-vibe",
        UpdateSource::Uv | UpdateSource::Pypi => "uv tool upgrade mistral-vibe",
    }
}

/// The post-dialog lines: which version to get and how, with every Vibe closed.
pub fn manual_update_lines(state: &UpdatePromptState) -> [String; 2] {
    [
        color_span(
            theme::text(theme::secondary()),
            &format!(
                "To update Vibe from {} to {}, close every running Vibe session (including IDE integrations), then run:",
                state.current_version, state.latest_version
            ),
        ),
        format!(
            "  {}",
            color_span(
                Style::default().add_modifier(Modifier::BOLD),
                manual_update_command(state.source)
            )
        ),
    ]
}

/// Print the instructions and exit: nothing ran, the user upgrades from a shell.
pub fn print_manual_update(state: &UpdatePromptState) -> Option<i32> {
    println!("{}", manual_update_lines(state).join("\n"));
    Some(0)
}
