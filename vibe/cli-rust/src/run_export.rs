//! How a headless run ended, and the export that records it.
//! The JSON contract is the app server's `vibe/app_server/run_export.py`.
//!
//! The models live in `model`, the export file and journal copy in `journal`,
//! and the config redaction in `redact`; this module is the public facade.

mod journal;
mod model;
mod redact;

pub use journal::{copy_journal, write_run_export};
pub use model::{
    turn_stop_from_turn, RunError, RunExport, RunLimits, RunOutcome, RunReport, RunResult,
    RunUsage, RunWarning, TurnStop,
};
pub use redact::redact_config;

pub const EXPORT_FILENAME: &str = "export.json";
pub const JOURNAL_DIRNAME: &str = "session";
/// The session writer's metadata file, whose config snapshot holds the
/// unredacted effective config.
pub const METADATA_FILENAME: &str = "meta.json";
