//! Slash commands: the table, dispatch, and the heavy /stress command.

pub mod clear;
#[allow(clippy::module_inception)]
mod commands;
pub mod compact;
mod dispatch;
pub mod event;
pub mod provider_auth;
pub mod retry;
pub mod retry_continuation;
pub mod retry_prompt;
pub mod shell;
pub mod simple;
pub mod stress;
pub mod submission;

pub use commands::{argument_hint, builtin, entries, has_command, is_side_channel, parse};
pub use event::CommandEvent;
