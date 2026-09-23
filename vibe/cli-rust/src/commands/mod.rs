//! Slash commands: the table, dispatch, and the heavy /stress command.

pub mod clear;
#[allow(clippy::module_inception)]
mod commands;
pub mod compact;
mod dispatch;
pub mod event;
pub mod retry;
pub mod retry_continuation;
pub mod retry_prompt;
pub mod shell;
pub mod simple;
pub mod stress;
pub mod submission;
pub(crate) mod usage;

pub use commands::{builtin, entries, is_side_channel, parse};
pub use event::CommandEvent;
