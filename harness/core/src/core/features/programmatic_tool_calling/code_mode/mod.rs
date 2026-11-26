//! Private code-mode evaluator.
//!
//! The rest of Core depends on this evaluator-neutral interface. Deno and V8
//! types, isolate setup, source generation, watchdogs, and transpilation stay
//! inside `v8`. Durable replay records live with the program state machine.

use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

use crate::core::features::programmatic_tool_calling::TypeScriptTool;
use crate::core::features::programmatic_tool_calling::model::PartialEvaluation;
use crate::core::features::programmatic_tool_calling::model::ToolState;
use crate::core::step_protocol::DeterminismContext;

mod v8;

pub(crate) use v8::MAX_SOURCE_BYTES;

pub(crate) struct EvaluationRequest<'a> {
    pub(crate) partial_evaluation: &'a PartialEvaluation,
    pub(crate) tools: &'a [TypeScriptTool],
    pub(crate) execution_id: &'a str,
    /// Earlier evaluation rounds of this program. Each one sent at least one
    /// new external operation, so this is a lower bound on the effects the
    /// program has already sent.
    pub(crate) round: u32,
    pub(crate) determinism: DeterminismContext,
    pub(crate) max_program_effects: u32,
    pub(crate) max_program_operations: u32,
    /// Set once the program used its time budget. A program that then runs
    /// a fresh step instead of a tool call stops with this error.
    pub(crate) budget_error: Option<Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum CodeResult {
    Success { value: Value },
    Error { error: Value },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum EvaluationOutcome {
    PartialEvaluation {
        partial_evaluation: PartialEvaluation,
        /// Output so far, for the summary of an interrupted program.
        #[serde(default, skip_serializing_if = "ProgramLogs::is_empty")]
        logs: ProgramLogs,
    },
    CodeResult {
        #[serde(skip_serializing_if = "Option::is_none")]
        stdout: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        stderr: Option<String>,
        tool_state: Vec<ToolState>,
        /// For each `console.log` line, the number of `tool_state` entries
        /// recorded before it.
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        log_positions: Vec<usize>,
        result: CodeResult,
    },
    Error {
        error: Value,
    },
}

/// A suspended program's output so far.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub(crate) struct ProgramLogs {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) stdout: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) stderr: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub(crate) log_positions: Vec<usize>,
}

impl ProgramLogs {
    fn is_empty(&self) -> bool {
        self.stdout.is_none() && self.stderr.is_none() && self.log_positions.is_empty()
    }
}

pub(crate) fn evaluate(request: EvaluationRequest<'_>) -> Result<EvaluationOutcome, String> {
    v8::evaluate(request)
}
