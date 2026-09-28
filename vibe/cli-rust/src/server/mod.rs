//! App-server JSON-RPC transport: wire types + concrete process client.
// The wire types are a `pub` surface in the library build, but the `vibe-rs`
// bin declares `mod server;` privately and only uses a subset of the fields.
// Suppress dead-code linting for this subtree so the bin compiles clean.
#![allow(dead_code)]

pub mod callback;
pub mod child;
pub mod effect;
pub mod effect_output;
pub mod images;
pub mod process;
pub mod proto_agents;
pub mod proto_approval;
pub mod proto_mcp;
pub mod proto_projects;
pub mod proto_questions;
pub mod proto_trust;
pub mod reader;
pub mod signal;
pub mod stderr;
pub mod types;

pub use child::{ChildHandle, SHUTDOWN_GRACE};
pub use effect::*;
pub use effect_output::*;
pub use images::*;
pub use process::{
    ActiveSession, Client, Launch, Notification, Pending, RequestFailure, DEFAULT_APP_SERVER_ARGS,
};
pub use proto_agents::*;
pub use proto_approval::*;
pub use proto_mcp::*;
pub use proto_questions::*;
pub use proto_trust::*;
pub use types::*;

// Explicit re-exports so `crate::server::method` etc. resolve regardless of
// how glob re-exports treat pub submodules.
pub use types::{method, notification, server_method, CALLBACK_KINDS};
