//! Compaction scenarios, split by concern; `support` holds the shared builders
//! and matchers.

use super::*;
use crate::core::testing::{
    add_always_hook, agent_request_estimate, assistant_text, compaction_action_id,
    summary_request_estimate,
};
use crate::core::{InputModality, Message};

mod check;
mod cut_off;
mod image;
mod rebuild;
mod refusal;
mod requests;
mod standalone;
mod support;

use support::*;
