//! Sign-in tools: the model asks the user to sign in to a source, such as a
//! connector, whose tools the Session needs.
//!
//! Core owns the tools' names, descriptions and schemas. A Runtime that can ask
//! its user to sign in offers them by setting `sign_in` in a capability set,
//! and runs them: the sign-in waits for the user's answer.

mod tools;

pub(crate) use tools::{
    LIST_TOOL_ID, REQUEST_TOOL_ID, ToolName, direct_tools, is_direct_name, offered,
    runtime_contracts,
};

/// The tool group name a caller cannot take: the first part of the sign-in
/// tools' namespace, `builtin.auth`.
pub(crate) const RESERVED_GROUP_NAME: &str = "builtin";
