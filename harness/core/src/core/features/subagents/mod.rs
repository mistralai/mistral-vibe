//! Model-facing agent operations.

mod agent_types;
mod prompts;
mod settings;
mod tools;

pub(crate) use agent_types::{AgentTypeDefinition, validate_agent_types};
pub(crate) use prompts::Prompts;
pub(crate) use settings::{Mode, validate_reconfiguration};
pub(crate) use tools::tools;

pub(crate) const NAMESPACE: &str = "agents";
