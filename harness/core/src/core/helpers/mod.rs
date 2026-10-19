//! Pure helpers shared by Core modules.

mod agent_message;
mod strings;

pub(crate) use agent_message::{
    AgentMessageContext, AgentMessageKind, AgentResultError, AgentResultTermination,
    render_agent_message,
};
pub(crate) use strings::normalized_similarity_score;
