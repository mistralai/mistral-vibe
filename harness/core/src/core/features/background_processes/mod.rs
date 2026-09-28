mod settings;
mod tools;

use crate::core::tools::command_environment::CommandEnvironmentProfile;

pub(crate) use settings::Mode;
pub(crate) use tools::{is_sandbox_name, tools};

pub(crate) const NAMESPACE: &str = "process";
pub(crate) const SEARCH_DESCRIPTION: &str =
    "Tools to start and interact with long-running background commands.";

pub(crate) fn prompt_section(profile: CommandEnvironmentProfile) -> String {
    format!(
        r#"## Background processes

Use background processes through the programmatic `tools.process` namespace when a command must keep running while you continue other work:
- `tools.process.start` accepts the same command syntax as `tools.file_system.{tool_name}`.
- `tools.process.start` starts a command and returns a stable process ID immediately.
- `tools.process.output` reads incremental output from a raw byte cursor and can wait for new output or termination, or reads the current newest page with `from: "end"`. Continue forward reads at `nextCursor`; `hasMore` reports unread retained output and `truncatedBefore` reports discarded history.
- `tools.process.write` sends text, control keys, or raw bytes to an interactive process.
- `tools.process.list` reports this session's processes.
- `tools.process.stop` terminates a process and its children.

Use ordinary `tools.file_system.{tool_name}` for commands whose complete result is needed immediately. Keep each returned process ID and output cursor. The Runtime injects a metadata-only notification when a process reaches a terminal state; call `tools.process.output` to inspect remaining output before drawing conclusions and stop processes that are no longer needed.

When background work needs a short pause, call `tools.self.sleep({{ seconds }})` through `run_typescript`, then inspect process output or status."#,
        tool_name = profile.tool_name,
    )
}
