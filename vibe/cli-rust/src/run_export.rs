//! How a headless run ended, and the export that records it.
//! The JSON contract is the app server's `vibe/app_server/run_export.py`.
//!
//! The models live in `model`, the export file and journal copies in
//! `journal`, the subagents' entries in `subagents`, and the config redaction
//! in `redact`; this module is the public facade.

mod journal;
mod model;
mod redact;
mod subagents;

pub use journal::{copy_journal, copy_subagent_journals, write_run_export};
pub use model::{
    turn_stop_from_turn, RunError, RunExport, RunLimits, RunOutcome, RunReport, RunResult,
    RunSubagent, RunSubagentStatus, RunUsage, RunWarning, SubagentReport, TurnStop,
};
pub use redact::redact_config;
pub use subagents::export_subagents;

pub const EXPORT_FILENAME: &str = "export.json";
pub const JOURNAL_DIRNAME: &str = "session";
/// Where each subagent's journal goes, under its session id.
pub const SUBAGENTS_DIRNAME: &str = "subagents";
/// The session writer's metadata file, whose config snapshot holds the
/// unredacted effective config.
pub const METADATA_FILENAME: &str = "meta.json";
