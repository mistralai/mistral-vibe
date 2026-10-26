//! Pure helpers shared by Core modules.

mod agent_message;
mod prompt_template;
mod schema_errors;
mod strings;

pub(crate) use agent_message::{
    AgentMessageContext, AgentMessageKind, AgentMessageSettings, AgentResultError,
    AgentResultTermination, render_agent_message,
};
pub(crate) use prompt_template::{render_template, validate_template};
pub(crate) use schema_errors::schema_error_detail;
pub(crate) use strings::normalized_similarity_score;
