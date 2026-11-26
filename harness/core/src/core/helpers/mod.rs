//! Pure helpers shared by Core modules.

mod agent_message;
mod escape_xml;
mod prompt_template;
mod schema_errors;
mod strings;

pub(crate) use agent_message::{
    AgentMessageContext, AgentMessageSettings, AgentResultError, AgentResultTermination,
    render_agent_message,
};
pub(crate) use escape_xml::escape_xml;
pub(crate) use prompt_template::{render_template, validate_template};
pub(crate) use schema_errors::{
    allowed_fields, schema_error_detail, schema_error_detail_with_fields,
    schema_error_detail_without_value,
};
pub(crate) use strings::{edit_distance, normalized_similarity_score};
