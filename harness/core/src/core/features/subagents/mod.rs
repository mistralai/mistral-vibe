//! Model-facing agent operations. Deprecated spellings are not executable routes.

mod agent_types;
mod replay;
mod settings;
mod tools;

pub(crate) use agent_types::{AgentTypeDefinition, validate_agent_types};
pub(crate) use replay::{recorded_program_name, rejected_tools, retired_namespaces};
pub(crate) use settings::{Mode, validate_reconfiguration};
pub(crate) use tools::tools;

pub(crate) const NAMESPACE: &str = "agents";
pub(crate) const SEARCH_DESCRIPTION: &str = "Tools to create and coordinate independent agents.";

pub(crate) fn prompt_section() -> &'static str {
    "## Agents\n\nUse the available functions in `tools.agents` through `run_typescript`. Each child has an independent Session and history. Spawn admits its first message without waiting for the answer. Child names are permanent single path segments below /root and cannot be reused. List shows admitted direct children, not provisional work. Agent operations are external effects."
}
