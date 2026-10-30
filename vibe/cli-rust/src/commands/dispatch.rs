//! Built-in slash-command dispatch.

use std::sync::Arc;

use tokio::sync::mpsc;

use super::clear::clear_history;
use super::simple::{
    add_text, copy_last_agent_message, help_text, read_log, reload_config, rename_session, whoami,
    DATA_RETENTION_MESSAGE,
};
use super::stress;
use super::submission::{clear_and_remember, NOTICE_TIMEOUT_SECS};
use crate::app::App;
use crate::server::Client;
use crate::{
    config, log_level_picker, mcp, model_picker, paste_image, resume_picker, rewind, theme_picker,
    ui,
};

pub(super) fn run_command(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    command: &str,
    value: &str,
) -> bool {
    clear_and_remember(app, value);
    crate::telemetry::slash_command_used(app, command, "builtin");
    match command {
        "/exit" => return true,
        "/config" => config::open(app, client, config_tx),
        "/release-notes" | "/whatsnew" => crate::release_notes::open(app),
        "/voice" => crate::voice_app::open(app),
        "/proxy-setup" => crate::proxy_setup::open(app, client),
        "/theme" => theme_picker::open(app),
        "/model" => model_picker::open(app),
        "/thinking" => crate::thinking_picker::open(app),
        "/remote-project" => crate::vibe_code_project::open(app, client),
        "/teleport" => crate::teleport::command(app, client),
        "/log-level" => log_level_picker::open(app),
        "/debug" => crate::debug_console::toggle(app, client),
        "/resume" | "/continue" => resume_picker::open(app, client),
        "/rewind" => rewind::command(app, client),
        "/branch" | "/fork" => super::branch::branch_session(app, client),
        "/mcp" | "/connectors" => mcp::show(app, client, value),
        "/plugins" => crate::plugins::show(app, client),
        "/reload-plugins" => crate::plugins::reload(app, client),
        "/leanstall" => super::leanstall::install_lean(app, client),
        "/unleanstall" => super::leanstall::uninstall_lean(app, client),
        "/help" => add_text(app, &help_text()),
        "/clear" | "/new" | "/clean" => clear_history(app, client, value),
        "/compact" => super::compact::start_compact(app, client, value),
        "/copy" => copy_last_agent_message(app),
        "/paste-image" => paste_image::request(app, true),
        "/status" => super::provider_auth::status(app, client),
        "/log" => read_log(app, client),
        "/rename" => rename_session(app, client, value),
        "/reload" => reload_config(app, client),
        "/retry" => {
            let args = command_args(value);
            super::retry::start_retry(app, client, args);
        }
        "/whoami" => whoami(app, client),
        "/data-retention" => add_text(app, DATA_RETENTION_MESSAGE),
        "/stress" => {
            let args = command_args(value);
            stress::toggle(app, client, args);
        }
        _ => ui::notice::show(
            app,
            &format!("Command {command} is not implemented"),
            NOTICE_TIMEOUT_SECS,
        ),
    }
    false
}

/// The command's arguments: input after the command word.
fn command_args(value: &str) -> &str {
    value
        .split_once(char::is_whitespace)
        .map(|(_, rest)| rest)
        .unwrap_or_default()
        .trim()
}
