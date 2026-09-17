//! Slash commands: the table, dispatch, and the heavy /demo and /stress commands.

pub mod clear;
#[allow(clippy::module_inception)]
mod commands;
pub mod compact;
pub mod demo;
mod dispatch;
pub mod event;
pub mod retry;
pub mod retry_continuation;
pub mod shell;
pub mod simple;
pub mod skill;
pub mod stress;
pub mod submission;

pub use commands::{builtin, entries, is_side_channel, parse};
pub use event::CommandEvent;
