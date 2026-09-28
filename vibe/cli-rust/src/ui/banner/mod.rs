//! Welcome banner: the animated braille cat plus an info block.

pub mod petit_chat;

use std::time::Instant;

use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use super::theme;
use crate::utils::startup_cache::StartupConfig;
use petit_chat::PetitChat;

/// Rust-only hint shown as its own line under the banner info block; suppressed
/// under the e2e replay harness so it never breaks parity with the Python CLI.
const RUST_BUILD_HINT: &str = "You are using the Rust version of Vibe (experimental).";

/// The welcome banner: an animated cat above a static info block.
#[derive(Default)]
pub struct Banner {
    chat: PetitChat,
    /// Plan label appended to the version line, from `account/read`.
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

    /// The full banner: the cat rows followed by the info block.
    pub fn view(&self, config: &StartupConfig) -> Vec<Line<'static>> {
        // `#banner-container` `padding: 1 1 0 0`: one blank row above the cat.
        let mut lines: Vec<Line> = vec![Line::from("")];
        lines.extend(
            self.chat
                .render()
                .lines()
                .map(|row| Line::from(Span::styled(row.to_string(), Style::default()))),
        );

        let meta = Style::default().fg(theme::foreground());
        let brand = Style::default()
            .fg(theme::ORANGE)
            .add_modifier(Modifier::BOLD);
        let cmd = Style::default().fg(theme::secondary());

        let plan = match &self.plan_title {
            Some(plan) => format!(" · {plan}"),
            None => String::new(),
        };
        lines.push(Line::from(""));
        lines.push(Line::from(vec![
            Span::styled("Mistral Vibe", brand),
            Span::raw(" "),
            Span::styled(
                format!("v{} · {}{plan}", config.server_version, config.active_model),
                meta,
            ),
        ]));
        lines.push(Line::from(Span::styled(meta_counts(config), meta)));
        lines.push(Line::from(vec![
            Span::styled("Type ", meta),
            Span::styled("/help", cmd),
            Span::styled(" for more information", meta),
        ]));
        if show_rust_hint() {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                RUST_BUILD_HINT,
                theme::text(theme::warning()),
            )));
        }
        // `.greeting-message` `margin-top: 1`: one blank row above the greeting.
        if let Some(greeting) = &self.greeting {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(greeting.clone(), meta)));
        }
        lines
    }
}

/// The Rust-only hint is shown in real runs. Under the e2e replay harness it is
/// suppressed to preserve parity with the Python CLI, unless a scenario opts back
/// in with `VIBE_TEST_SHOW_RUST_HINT` so one golden can capture its appearance.
fn show_rust_hint() -> bool {
    !crate::utils::is_replaying() || std::env::var_os("VIBE_TEST_SHOW_RUST_HINT").is_some()
}

fn meta_counts(config: &StartupConfig) -> String {
    if config.models_count == 0 {
        return String::new();
    }
    let mut parts = vec![plural(config.models_count, "model")];
    parts.push(if config.connectors_total != config.connectors_connected {
        format!(
            "{}/{} connector{}",
            config.connectors_connected,
            config.connectors_total,
            if config.connectors_total == 1 {
                ""
            } else {
                "s"
            }
        )
    } else {
        plural(config.connectors_connected, "connector")
    });
    parts.push(if config.mcp_servers_enabled != config.mcp_servers_total {
        format!(
            "{}/{} MCP server{}",
            config.mcp_servers_enabled,
            config.mcp_servers_total,
            if config.mcp_servers_total == 1 {
                ""
            } else {
                "s"
            }
        )
    } else {
        plural(config.mcp_servers_enabled, "MCP server")
    });
    parts.push(plural(config.skills_count, "skill"));
    if config.hooks_count > 0 {
        parts.push(plural(config.hooks_count, "hook"));
    }
    parts.join(" · ")
}

fn plural(count: usize, singular: &str) -> String {
    format!("{count} {singular}{}", if count == 1 { "" } else { "s" })
}
